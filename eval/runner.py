"""Eval runner — wires (dataset x fault x agent) -> metrics.

Week 2 scope:
  - Jaffle Shop only (more datasets in Week 4).
  - 3 fault classes: null_spike, duplicate_ingestion, broken_join_dropout.
  - SqlglotWalker (real lineage via parsed compiled SQL).
  - No classifier yet: the runner reads `fault.cause_class` directly to
    build the prediction. This is still a tautology — classification F1
    is meaningful only once the Week-3 classifier replaces this line.
  - Scores against ground truth and writes REPORT.md.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from dq_triage.attribution.manifest import Manifest
from dq_triage.attribution.sqlglot_walker import build_walker
from dq_triage.models import GroundTruth
from eval import dbt_runner
from eval.datasets import JAFFLE_SHOP
from eval.datasets.jaffle_shop import DatasetConfig
from eval.faults.base import Fault
from eval.faults.broken_join_dropout import ALL_BROKEN_JOIN_PATTERNS
from eval.faults.duplicate_ingestion import ALL_DUPE_PATTERNS
from eval.faults.null_spike import ALL_NULL_SPIKE_PATTERNS
from eval.ground_truth import write_jsonl as write_truths_jsonl
from eval.metrics import Prediction, compute

console = Console()


# ---------------------------------------------------------------------------
# Suite definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Trial:
    fault: Fault
    seed: int


def build_suite(suite: str, cfg: DatasetConfig = JAFFLE_SHOP) -> list[Trial]:
    """Return the trials for a named suite.

    Three fault families, each with three patterns, each with N seeds:
      smoke -> N=2  (3 x 3 x 2 = 18 trials, ~1 min)
      full  -> N=5  (3 x 3 x 5 = 45 trials, ~3-4 min)
    """
    if suite == "smoke":
        seeds_per = 2
    elif suite == "full":
        seeds_per = 5
    else:
        raise SystemExit(f"unknown suite: {suite}")

    # (fault_class_list, target) pairs.
    families: list[tuple[list[type], object]] = [
        (ALL_NULL_SPIKE_PATTERNS, cfg.null_spike_target),
        (ALL_DUPE_PATTERNS, cfg.duplicate_ingestion_target),
        (ALL_BROKEN_JOIN_PATTERNS, cfg.broken_join_dropout_target),
    ]
    trials: list[Trial] = []
    for fault_classes, target in families:
        for cls in fault_classes:
            for s in range(1, seeds_per + 1):
                trials.append(Trial(fault=cls(target), seed=s))
    return trials


# ---------------------------------------------------------------------------
# Per-trial loop
# ---------------------------------------------------------------------------


def run_trial(trial: Trial) -> tuple[GroundTruth, Prediction | None, dict]:
    """Returns (ground_truth, prediction-or-None, diagnostics)."""
    cfg = JAFFLE_SHOP
    duckdb_path = cfg.duckdb_path
    # 1. seed (resets raw_* tables)
    dbt_runner.seed(cfg.dbt_project_dir, duckdb_path)
    # 2. apply fault to mutate raw
    with duckdb.connect(str(duckdb_path)) as con:
        fault_result = trial.fault.apply(con, cfg.name, trial.seed)
    gt = fault_result.ground_truth
    # 3. dbt build (will fail some tests)
    t0 = time.perf_counter()
    build_result = dbt_runner.build(cfg.dbt_project_dir, duckdb_path)
    build_seconds = time.perf_counter() - t0
    if not build_result.failures:
        # No tests broke — fault wasn't effective; treat as no-prediction.
        return gt, None, {"stealth": True, "build_s": build_seconds}

    # 4. Pick the failing test closest to the fault.
    #    Strategy: prefer the test the fault's FaultTarget *expects* to break
    #    (matched by prefix to tolerate dbt hash-truncation on long
    #    relationship test names). If nothing matches, fall back to the
    #    "most upstream" heuristic (prefer stg_*).
    expected_prefix = getattr(trial.fault, "target", None)
    expected_test = (
        expected_prefix.expected_failing_test if expected_prefix is not None else None
    )
    failing = list(build_result.failures)
    chosen = None
    if expected_test:
        for f in failing:
            if f.test_name.startswith(expected_test):
                chosen = f
                break
    if chosen is None:
        chosen = sorted(
            failing,
            key=lambda f: (0 if f.model.startswith("stg_") else 1, f.model, f.test_name),
        )[0]

    # 5. Load failing row PKs from the failures table.
    #
    # dbt writes a *canonical* schema to test failures tables depending on
    # the generic test:
    #   - not_null:       full failing row, so pk_col is present directly
    #   - unique:         columns = (unique_field, n_records) — the value
    #                     IS the duplicated column, which for our staging
    #                     models equals the PK
    #   - relationships:  column = from_field — the orphan FK value; we
    #                     translate FK -> PK by joining back into the
    #                     failing model
    pk_col = _pk_col_for_model(chosen.model)
    failing_pks: tuple[str, ...] = ()
    # Build a fully-qualified relation for `chosen.model` from the manifest
    # — staging models live in `main_staging`, marts in `main_marts`, etc.,
    # so an unqualified name won't resolve in DuckDB.
    manifest = Manifest(cfg.dbt_project_dir)
    chosen_node = manifest.by_name.get(chosen.model)
    chosen_relation = (
        f"{chosen_node.schema}.{chosen_node.alias}"
        if chosen_node is not None
        else chosen.model
    )
    with duckdb.connect(str(duckdb_path), read_only=True) as con:
        try:
            failing_rows = con.execute(
                f"SELECT * FROM {chosen.failures_table_fqn} LIMIT 1000"
            ).fetchdf()
        except duckdb.CatalogException:
            failing_rows = None

        if failing_rows is None or failing_rows.empty:
            failing_pks = ()
        elif pk_col in failing_rows.columns:
            # not_null: full row stored, PK is present.
            failing_pks = tuple(str(v) for v in failing_rows[pk_col].tolist())
        elif "unique_field" in failing_rows.columns:
            # unique test: the duplicated column value == PK for staging
            # models where the tested column is the model's PK.
            failing_pks = tuple(
                str(v) for v in failing_rows["unique_field"].tolist()
            )
        elif "from_field" in failing_rows.columns:
            # relationships test: orphan FK values. Translate FK -> PK by
            # joining back into the failing model.
            fk_values = failing_rows["from_field"].tolist()
            if fk_values and chosen.column:
                placeholders = ",".join(["?"] * len(fk_values))
                rows = con.execute(
                    f"SELECT {pk_col} FROM {chosen_relation} "
                    f"WHERE {chosen.column} IN ({placeholders})",
                    fk_values,
                ).fetchall()
                failing_pks = tuple(str(r[0]) for r in rows)
        elif chosen.column and chosen.column in failing_rows.columns:
            # Last-ditch fallback: the failures table has the column name
            # directly (e.g. older dbt versions). Translate to PK.
            fk_values = failing_rows[chosen.column].tolist()
            if fk_values:
                placeholders = ",".join(["?"] * len(fk_values))
                rows = con.execute(
                    f"SELECT {pk_col} FROM {chosen_relation} "
                    f"WHERE {chosen.column} IN ({placeholders})",
                    fk_values,
                ).fetchall()
                failing_pks = tuple(str(r[0]) for r in rows)

    # 6. Attribute. W2: SQLGlot walker (was ThinAttributor in W1).
    attributor = build_walker(cfg.dbt_project_dir)

    with duckdb.connect(str(duckdb_path), read_only=True) as con:
        blame = attributor.attribute(
            con,
            failing_model=chosen.model,
            failing_column=chosen.column or "",
            failing_pks=failing_pks,
            failing_pk_column=pk_col,
        )

    # 7. Build Prediction. W2: cause_class is read from the fault itself.
    #    This is *still a tautology* for classification — the W3 classifier
    #    will replace this line with rule-based scoring over upstream stats.
    prediction = Prediction(
        incident_key=gt.incident_key,
        candidate_tables=(blame.model,),
        blame_column=blame.column,
        blame_row_pks=frozenset(blame.row_pks),
        cause_class=trial.fault.cause_class,
        confidence=blame.certainty,
        latency_seconds=build_seconds,
    )
    return gt, prediction, {
        "chosen_test": chosen.test_name,
        "n_failing_pks": len(failing_pks),
        "build_s": build_seconds,
    }


def _pk_col_for_model(model: str) -> str:
    """Week-1 hardcode. The Attributor knows row-identity mappings, but the
    runner needs to know the PK column name in each model to fetch failing PKs."""
    return {
        "stg_customers": "customer_id",
        "stg_orders": "order_id",
        "stg_payments": "payment_id",
        "customers": "customer_id",
        "orders": "order_id",
        "raw_customers": "id",
        "raw_orders": "id",
        "raw_payments": "id",
    }.get(model, "id")


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


def run(suite: str, report_path: Path) -> int:
    trials = build_suite(suite)
    console.rule(f"[bold]Suite: {suite}  ({len(trials)} trials)")
    truths: list[GroundTruth] = []
    preds: list[Prediction] = []
    stealth = 0
    failed_attributions = 0
    diag: list[dict] = []

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("trials", total=len(trials))
        for trial in trials:
            gt, pred, info = run_trial(trial)
            truths.append(gt)
            if pred is None:
                if info.get("stealth"):
                    stealth += 1
                else:
                    failed_attributions += 1
            else:
                preds.append(pred)
            diag.append({"seed": trial.seed, "pattern": trial.fault.pattern_id, **info})
            progress.advance(task)

    # Score
    from eval.metrics import pair as pair_fn
    pairs = pair_fn(preds, truths)
    report = compute(pairs)

    # Write artefacts
    run_dir = report_path.parent / "runs" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    write_truths_jsonl(run_dir / "ground_truth.jsonl", truths)
    (run_dir / "diagnostics.txt").write_text("\n".join(repr(d) for d in diag))

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_render_report(
        suite=suite,
        report=report,
        n_trials=len(trials),
        n_preds=len(preds),
        stealth=stealth,
        failed_attributions=failed_attributions,
        run_dir=run_dir,
    ))

    console.print()
    console.print(f"[green]✓[/] Report → {report_path}")
    console.print(f"  • Trials: {len(trials)}  Predictions: {len(preds)}  Stealth: {stealth}  No-attribution: {failed_attributions}")
    console.print(f"  • Top-1 table acc: {report.top1_table_acc:.1%}   Row recall: {report.row_recall:.2f}   Median latency: {report.median_latency_s:.2f}s")
    return 0


def _render_report(
    suite: str,
    report,
    n_trials: int,
    n_preds: int,
    stealth: int,
    failed_attributions: int,
    run_dir: Path,
) -> str:
    from eval.metrics import MetricsReport

    per_class_lines = "\n".join(
        f"| `{cls.value}` | {acc:.1%} |"
        for cls, acc in sorted(report.per_class_accuracy.items(), key=lambda kv: kv[0].value)
    )
    return (
        f"# Eval Report — {suite}\n\n"
        f"_generated: {datetime.now(UTC).isoformat()}_\n\n"
        f"## Suite\n\n"
        f"- Dataset: **jaffle_shop**\n"
        f"- Trials: **{n_trials}**\n"
        f"- Predictions emitted: **{n_preds}**\n"
        f"- Stealth faults (no test broke): **{stealth}**\n"
        f"- No-attribution failures: **{failed_attributions}**\n"
        f"- Run dir: `{run_dir.relative_to(Path.cwd()) if run_dir.is_relative_to(Path.cwd()) else run_dir}`\n\n"
        f"## Results\n\n"
        + MetricsReport.markdown_header() + "\n"
        + report.as_markdown_row("SqlglotWalker (W2)") + "\n\n"
        f"## Per-class accuracy\n\n"
        f"| Class | Accuracy |\n|---|---|\n{per_class_lines}\n\n"
        "## Honesty disclaimer (Week 2)\n\n"
        "What changed since W1:\n\n"
        "- **Lineage is no longer hardcoded.** `SqlglotWalker` reads compiled\n"
        "  SQL from `target/compiled/` and uses `sqlglot.lineage` to follow\n"
        "  columns upstream through CTEs and across dbt-model boundaries.\n"
        "- **Three fault families** (was one): `upstream_null_spike`,\n"
        "  `duplicate_ingestion`, `broken_join_dropout` — 3 patterns each.\n"
        "  This stresses the walker on `unique` and `relationships` tests,\n"
        "  not just `not_null`.\n\n"
        "What is **still a tautology / still untested**:\n\n"
        "- **Classification.** The runner reads `fault.cause_class` directly\n"
        "  when building each `Prediction`. So the classification F1 is the\n"
        "  trivial answer — meaningful only when Week 3 replaces this with a\n"
        "  rules-based classifier and the 0% classifier baseline.\n"
        "- **One dataset.** Generalization across dbt projects lands in Week 4\n"
        "  (TPC-H + NYC-taxi).\n"
        "- **The hard case.** For `broken_join_dropout` the attributor lands\n"
        "  on the *child* table (orphan FK side), not the *parent* (where the\n"
        "  delete happened). That's correct attribution behaviour — telling\n"
        "  null-spike from join-dropout, same blame location, is the\n"
        "  classifier's job. The 100% top-1 here measures attribution, not\n"
        "  root-cause identification.\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="DQ Triage eval runner")
    parser.add_argument("--suite", choices=("smoke", "full"), default="smoke")
    parser.add_argument("--seeds", type=int, default=1, help="ignored in W1")
    parser.add_argument("--report", type=Path, default=Path("eval/REPORT.md"))
    args = parser.parse_args()
    return run(args.suite, args.report)


if __name__ == "__main__":
    raise SystemExit(main())
