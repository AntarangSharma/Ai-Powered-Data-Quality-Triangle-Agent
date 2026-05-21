"""Eval runner — wires (dataset × fault × agent) → metrics.

Week 1 scope:
  - Jaffle Shop only
  - null_spike faults only
  - ThinAttributor (hardcoded one-level lineage)
  - No classifier; class is hardcoded to UPSTREAM_NULL_SPIKE for now
  - Scores against ground truth and writes REPORT.md
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

from dq_triage.attribution.sqlglot_walker import build_walker
from dq_triage.models import GroundTruth, RootCauseClass
from eval import dbt_runner
from eval.datasets import JAFFLE_SHOP
from eval.faults.base import Fault
from eval.faults.null_spike import (
    NullSpikeConditional,
    NullSpikeFlat5pct,
    NullSpikeHeavy30pct,
)
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


def build_suite(suite: str) -> list[Trial]:
    """Return the trials for a named suite."""
    target = JAFFLE_SHOP.null_spike_target
    fault_classes = [NullSpikeFlat5pct, NullSpikeHeavy30pct, NullSpikeConditional]
    if suite == "smoke":
        seeds_per = 3  # 3 patterns × 3 seeds = 9 trials
    elif suite == "full":
        seeds_per = 10  # 3 × 10 = 30 trials for W1
    else:
        raise SystemExit(f"unknown suite: {suite}")
    trials: list[Trial] = []
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

    # 4. Pick the most-upstream failing test (closest to source).
    #    Heuristic for W1: prefer tests on `stg_*` over `marts/*`.
    failing = sorted(
        build_result.failures,
        key=lambda f: (0 if f.model.startswith("stg_") else 1, f.model, f.test_name),
    )
    chosen = failing[0]

    # 5. Load failing row PKs from the failures table.
    with duckdb.connect(str(duckdb_path), read_only=True) as con:
        try:
            failing_rows = con.execute(
                f"SELECT * FROM {chosen.failures_table_fqn} LIMIT 1000"
            ).fetchdf()
        except duckdb.CatalogException:
            failing_rows = None

    # 6. Attribute. W2: SQLGlot walker (was ThinAttributor in W1).
    attributor = build_walker(cfg.dbt_project_dir)
    pk_col = _pk_col_for_model(chosen.model)
    failing_pks: tuple[str, ...] = ()
    if failing_rows is not None and pk_col in failing_rows.columns:
        failing_pks = tuple(str(v) for v in failing_rows[pk_col].tolist())

    with duckdb.connect(str(duckdb_path), read_only=True) as con:
        blame = attributor.attribute(
            con,
            failing_model=chosen.model,
            failing_column=chosen.column or "",
            failing_pks=failing_pks,
            failing_pk_column=pk_col,
        )

    # 7. Build Prediction. W1: hardcoded class (we only ship null_spike faults).
    prediction = Prediction(
        incident_key=gt.incident_key,
        candidate_tables=(blame.model,),
        blame_column=blame.column,
        blame_row_pks=frozenset(blame.row_pks),
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
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
        "## ⚠️ Honesty disclaimer (Week 2)\n\n"
        "These numbers are still easy. What has and has NOT changed since W1:\n\n"
        "- **Lineage is no longer hardcoded.** `SqlglotWalker` reads compiled SQL\n"
        "  from `target/compiled/` and uses `sqlglot.lineage` to follow columns\n"
        "  upstream through CTEs and across dbt-model boundaries. The same\n"
        "  100% / 1.0 numbers now reflect real parsing, not a lookup table.\n"
        "- **One fault class** (`upstream_null_spike`) — the runner still\n"
        "  hardcodes the predicted class. Classification F1 remains a tautology\n"
        "  until Week 3 lands the rules-based classifier.\n"
        "- **One fault target** (`raw_orders.user_id`). Generalization across\n"
        "  columns and dbt projects is still untested; more fault classes and a\n"
        "  ~40-incident benchmark land later in Week 2.\n\n"
        "The point of these reports is to **prove the loop works end-to-end**\n"
        "and to track delta as we replace components. 100% on the W1 suite is a\n"
        "necessary condition, not a sufficient one.\n"
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
