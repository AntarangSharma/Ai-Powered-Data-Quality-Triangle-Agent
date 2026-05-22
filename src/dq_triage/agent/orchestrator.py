"""User-facing triage orchestrator.

Wires the same pipeline the eval runner exercises into a callable that any
user (CLI, FastAPI handler, Airflow on-failure hook, …) can invoke when a
dbt test fails:

    dbt run_results.json  ──►  pick failing test
                                │
                                ▼
                          SqlglotWalker.attribute   (row-level blame)
                                │
                                ▼
                          assemble_evidence         (warehouse probes)
                                │
                                ▼
                          Classifier.classify       (deterministic rules)
                                │
                                ▼
                          Incident (frozen)         (persisted via store)

This module owns *no* business logic that isn't already in
:mod:`dq_triage.attribution`, :mod:`dq_triage.agent.evidence`,
:mod:`dq_triage.classification`, or :mod:`dq_triage.store`. It just glues
them in the right order, with neutral fallbacks when a step is unavailable
(e.g. no test_failures table → empty PK list, walker still runs).

Why not import the eval runner's logic directly:
  * eval/runner.py is benchmark scaffolding (loops, scoring, REPORT.md).
  * Production triage runs once per incident, persists, returns.
  * Keeping them separate means breaking the eval harness can't break the
    user-facing path.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from dq_triage.agent.evidence import ClassifierEvidence, assemble_evidence
from dq_triage.attribution.manifest import Manifest
from dq_triage.attribution.sqlglot_walker import build_walker
from dq_triage.classification import Classifier
from dq_triage.models import (
    BlameLocation,
    ClassScore,
    Incident,
    RootCauseClass,
    RootCauseHypothesis,
    Verdict,
)

# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FailingTest:
    """Minimal description of a failing dbt test.

    Sufficient to drive triage without re-running dbt. Populated either by
    :func:`load_failing_tests` (from run_results.json) or by the caller
    (e.g. an Airflow on-failure hook passing the test it just saw fail).
    """

    test_name: str
    model: str
    column: str | None
    failures_table_fqn: str  # e.g. "main_dbt_test_failures.not_null_..."
    kind: str  # not_null | unique | accepted_values | relationships
    parent_model: str | None = None
    parent_column: str | None = None


# ---------------------------------------------------------------------------
# run_results.json parsing
# ---------------------------------------------------------------------------


def load_failing_tests(project_dir: Path) -> list[FailingTest]:
    """Parse ``target/run_results.json`` + ``target/manifest.json`` and
    return the failing tests in the order dbt reported them.

    Returns ``[]`` if dbt was run successfully (no failures) or hasn't been
    run yet. Callers should treat that as "nothing to triage".
    """
    rr_path = project_dir / "target" / "run_results.json"
    mf_path = project_dir / "target" / "manifest.json"
    if not rr_path.exists() or not mf_path.exists():
        return []
    run_results = json.loads(rr_path.read_text())
    manifest = json.loads(mf_path.read_text())

    out: list[FailingTest] = []
    for r in run_results.get("results", []):
        unique_id = r.get("unique_id", "")
        if not unique_id.startswith("test."):
            continue
        if r.get("status") not in {"fail", "error", "warn"}:
            continue
        node = manifest["nodes"].get(unique_id, {})
        test_name = node.get("name", unique_id.split(".")[-1])
        depends_on = node.get("depends_on", {}).get("nodes", [])
        ref_models = [d.split(".")[-1] for d in depends_on if d.startswith("model.")]
        kind = "unknown"
        for k in ("not_null", "unique", "accepted_values", "relationships"):
            if test_name.startswith(f"{k}_"):
                kind = k
                break
        model, column = _parse_test_name(test_name, ref_models, kind)
        parent_model = None
        parent_column = None
        if kind == "relationships":
            for m in ref_models:
                if m != model:
                    parent_model = m
                    break
            tm = node.get("test_metadata") or {}
            kwargs = tm.get("kwargs") or {}
            parent_column = kwargs.get("field") or column
        relation = r.get("relation_name") or node.get("relation_name") or ""
        out.append(
            FailingTest(
                test_name=test_name,
                model=model,
                column=column,
                failures_table_fqn=relation.replace('"', ""),
                kind=kind,
                parent_model=parent_model,
                parent_column=parent_column,
            )
        )
    return out


def _parse_test_name(
    test_name: str, depends_on_models: list[str], kind: str
) -> tuple[str, str | None]:
    """Recover (model, column) from a dbt-generated test name. Longest
    model-name match wins (handles `stg` vs `stg_orders` ambiguity)."""
    if not depends_on_models or kind == "unknown":
        return depends_on_models[0] if depends_on_models else "?", None
    candidates = []
    for m in depends_on_models:
        pref = f"{kind}_{m}_"
        if test_name.startswith(pref):
            rest = test_name[len(pref) :]
            column = rest if "__" not in rest else rest.split("__")[0]
            candidates.append((len(m), m, column or None))
    if candidates:
        candidates.sort(reverse=True)
        _, model, picked_column = candidates[0]
        return model, picked_column
    return depends_on_models[0], None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


# Dataset-specific PK column conventions. Promote to a config file once we
# support more than Jaffle Shop (Week 4). The fallback ``id`` is what every
# generated dataset we've seen uses.
_DEFAULT_PK_MAP: dict[str, str] = {
    "stg_customers": "customer_id",
    "stg_orders": "order_id",
    "stg_payments": "payment_id",
    "customers": "customer_id",
    "orders": "order_id",
    "raw_customers": "id",
    "raw_orders": "id",
    "raw_payments": "id",
}


def _pk_col_for_model(model: str, overrides: dict[str, str] | None = None) -> str:
    if overrides and model in overrides:
        return overrides[model]
    return _DEFAULT_PK_MAP.get(model, "id")


# ---------------------------------------------------------------------------
# The main entry point
# ---------------------------------------------------------------------------


def triage(
    *,
    project_dir: Path,
    duckdb_path: Path,
    failing_test: FailingTest,
    pk_overrides: dict[str, str] | None = None,
) -> Incident:
    """Run the full triage pipeline against a single failing test.

    Idempotent / read-only against the warehouse (uses a read-only DuckDB
    connection). Caller is responsible for persistence — see
    :func:`triage_and_persist`.
    """
    t0 = time.perf_counter()
    manifest = Manifest(project_dir)
    walker = build_walker(project_dir)

    pk_col = _pk_col_for_model(failing_test.model, pk_overrides)
    chosen_node = manifest.by_name.get(failing_test.model)
    chosen_relation = (
        f"{chosen_node.schema}.{chosen_node.alias}"
        if chosen_node is not None
        else failing_test.model
    )

    # 1. Load failing PKs from the dbt failures table.
    failing_pks = _load_failing_pks(
        duckdb_path=duckdb_path,
        failing_test=failing_test,
        chosen_relation=chosen_relation,
        pk_col=pk_col,
    )

    # 2. Walk lineage to the raw side.
    with duckdb.connect(str(duckdb_path), read_only=True) as con:
        blame = walker.attribute(
            con,
            failing_model=failing_test.model,
            failing_column=failing_test.column or "",
            failing_pks=failing_pks,
            failing_pk_column=pk_col,
        )

    # 3. Resolve the parent raw side for relationships tests (so the
    #    orphan-FK probe has somewhere to point).
    parent_raw_table: str | None = None
    parent_raw_column: str | None = None
    if (
        failing_test.kind == "relationships"
        and failing_test.parent_model
        and failing_test.parent_column
    ):
        parent_pk_col = _pk_col_for_model(failing_test.parent_model, pk_overrides)
        with duckdb.connect(str(duckdb_path), read_only=True) as con:
            parent_blame = walker.attribute(
                con,
                failing_model=failing_test.parent_model,
                failing_column=failing_test.parent_column,
                failing_pks=(),
                failing_pk_column=parent_pk_col,
            )
        parent_raw_table = parent_blame.model
        parent_raw_column = parent_blame.column or _pk_col_for_model(
            parent_blame.model, pk_overrides
        )

    # 4. Probe upstream stats → ClassifierEvidence.
    with duckdb.connect(str(duckdb_path), read_only=True) as con:
        evidence = assemble_evidence(
            con,
            failing_test_kind=failing_test.kind,
            failing_model=failing_test.model,
            failing_column=failing_test.column,
            blame_model=blame.model,
            blame_column=blame.column,
            blame_pk_column=_pk_col_for_model(blame.model, pk_overrides),
            parent_table=parent_raw_table,
            parent_pk_column=parent_raw_column,
        )

    # 5. Classify.
    class_scores = Classifier().classify(evidence)
    top = class_scores[0]
    verdict_type = _verdict_for(class_scores)
    elapsed = time.perf_counter() - t0

    # 6. Build the Incident.
    from dq_triage.classification.calibration import calibrate

    hypothesis = RootCauseHypothesis(
        cause_class=top.cause_class,
        blame_model=blame.model,
        blame_column=blame.column,
        blame_rows_sample=(),
        confidence=top.score,
        confidence_calibrated=calibrate(top.score),
        evidence_summary=_evidence_summary(evidence, top),
        suggested_one_line_fix=_suggested_fix(top.cause_class, blame),
    )
    final_verdict = hypothesis if verdict_type is not Verdict.TRIAGE_ONLY else None

    return Incident(
        incident_id=f"inc_{uuid.uuid4().hex[:12]}",
        created_at=datetime.now(UTC),
        failing_test_name=failing_test.test_name,
        failing_model=failing_test.model,
        failing_column=failing_test.column,
        failing_row_count=len(failing_pks),
        failing_rows_sample=(),
        blame_location=blame,
        upstream_stats=(),  # populated in W5 when we wire historical stats
        class_scores=tuple(class_scores),
        hypotheses=(hypothesis,),
        verdict_type=verdict_type,
        final_verdict=final_verdict,
        time_to_verdict_seconds=elapsed,
        token_cost_usd=0.0,  # rules-only; no LLM call
    )


def triage_and_persist(
    *,
    project_dir: Path,
    duckdb_path: Path,
    failing_test: FailingTest,
    pk_overrides: dict[str, str] | None = None,
) -> Incident:
    """Run triage and persist the resulting Incident via the store."""
    from dq_triage.store import save_incident

    incident = triage(
        project_dir=project_dir,
        duckdb_path=duckdb_path,
        failing_test=failing_test,
        pk_overrides=pk_overrides,
    )
    save_incident(incident)
    return incident


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _load_failing_pks(
    *,
    duckdb_path: Path,
    failing_test: FailingTest,
    chosen_relation: str,
    pk_col: str,
) -> tuple[str, ...]:
    """Read the dbt failures table and translate to model PKs.

    Mirrors the runner's W2 fix: dbt's failures-table schema depends on
    test kind (not_null = full row, unique = ``unique_field``,
    relationships = ``from_field``)."""
    with duckdb.connect(str(duckdb_path), read_only=True) as con:
        try:
            failing_rows = con.execute(
                f"SELECT * FROM {failing_test.failures_table_fqn} LIMIT 1000"
            ).fetchdf()
        except duckdb.CatalogException:
            return ()
        if failing_rows is None or failing_rows.empty:
            return ()
        if pk_col in failing_rows.columns:
            return tuple(str(v) for v in failing_rows[pk_col].tolist())
        if "unique_field" in failing_rows.columns:
            return tuple(str(v) for v in failing_rows["unique_field"].tolist())
        if "from_field" in failing_rows.columns and failing_test.column:
            fk_values = failing_rows["from_field"].tolist()
            if not fk_values:
                return ()
            placeholders = ",".join(["?"] * len(fk_values))
            rows = con.execute(
                f"SELECT {pk_col} FROM {chosen_relation} "
                f"WHERE {failing_test.column} IN ({placeholders})",
                fk_values,
            ).fetchall()
            return tuple(str(r[0]) for r in rows)
    return ()


def _verdict_for(scores: tuple[ClassScore, ...] | list[ClassScore]) -> Verdict:
    """Map (top-1 score, top-2 gap) to a :class:`Verdict`.

    Thresholds match what the README promises:
      * AUTO         : top-1 ≥ 0.85 AND top-1 minus top-2 ≥ 0.15
      * TWO_CANDIDATE: top-1 ≥ 0.6  (close call → surface both)
      * TRIAGE_ONLY  : everything else (no verdict, surface evidence)
    """
    if not scores:
        return Verdict.TRIAGE_ONLY
    top = scores[0].score
    second = scores[1].score if len(scores) > 1 else 0.0
    if top >= 0.85 and (top - second) >= 0.15:
        return Verdict.AUTO
    if top >= 0.6:
        return Verdict.TWO_CANDIDATE
    return Verdict.TRIAGE_ONLY


def _evidence_summary(evidence: ClassifierEvidence, top: ClassScore) -> str:
    """Single-sentence explainer that names the signal the top class fired on."""
    cls = top.cause_class
    if cls is RootCauseClass.UPSTREAM_NULL_SPIKE:
        return (
            f"Blame column shows {evidence.blame_null_rate:.1%} null rate "
            f"over {evidence.blame_row_count} rows."
        )
    if cls is RootCauseClass.DUPLICATE_INGESTION:
        return (
            f"Blame PK has {evidence.blame_pk_dupe_count} duplicate occurrence(s) "
            f"in {evidence.blame_row_count} rows."
        )
    if cls is RootCauseClass.BROKEN_JOIN_DROPOUT:
        return (
            f"{evidence.orphan_fk_count} orphan FK value(s) in blame column "
            f"have no matching parent row."
        )
    return f"No deterministic detector fired (score {top.score:.2f})."


def _suggested_fix(cls: RootCauseClass, blame: BlameLocation) -> str:
    """One-line remediation hint. Heuristic — refined by the LLM narrator in W4."""
    where = f"{blame.model}.{blame.column}" if blame.column else blame.model
    if cls is RootCauseClass.UPSTREAM_NULL_SPIKE:
        return f"Backfill or filter NULLs in {where}; check upstream extract."
    if cls is RootCauseClass.DUPLICATE_INGESTION:
        return f"De-duplicate {where} before downstream models consume it."
    if cls is RootCauseClass.BROKEN_JOIN_DROPOUT:
        return f"Restore deleted parent rows referenced by {where}, or soft-delete instead."
    return f"Inspect {where} manually — no rule fired."
