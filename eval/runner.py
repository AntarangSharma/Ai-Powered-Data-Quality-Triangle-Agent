"""Eval runner — wires (dataset, fault, agent) → metrics.

Week 1 scope: stub. We will fill in dbt invocation + agent execution as
those modules come online. For now this gives the CLI surface that the
Makefile + CI expect.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from eval.metrics import MetricsReport


def main() -> int:
    parser = argparse.ArgumentParser(description="DQ Triage eval runner")
    parser.add_argument(
        "--suite",
        choices=("smoke", "full"),
        default="smoke",
        help="smoke = 30 incidents subset, full = entire benchmark",
    )
    parser.add_argument("--seeds", type=int, default=1)
    parser.add_argument("--report", type=Path, default=Path("eval/REPORT.md"))
    args = parser.parse_args()

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        "# Eval Report\n\n"
        f"_suite: {args.suite}, seeds: {args.seeds}_\n\n"
        "## Status\n\n"
        "Runner stub — no agent or fault execution wired yet (Week 1, day 1).\n\n"
        "## Results\n\n"
        + MetricsReport.markdown_header()
        + "\n"
        + "| (no runs yet) | — | — | — | — | — | — | — | — |\n"
    )
    print(f"Wrote stub report to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
