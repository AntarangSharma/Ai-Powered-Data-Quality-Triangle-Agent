# DQ Triage Agent

> **Status:** Week 0 — scaffolding. No working agent yet. Track progress in [`docs/02_revised_plan.md`](docs/02_revised_plan.md).

When a dbt test fails, this agent walks **column-level lineage upstream**, finds the exact source rows that caused the failure, classifies the root cause across 10 patterns, and posts a Slack thread with a one-line fix.

Not an anomaly detector. **Root-cause attribution with row-level precision.**

## Why this exists

Current OSS data-quality tools (Elementary, re_data, dbt tests alone) stop at **detection**. They tell you *that* something failed. They don't tell you *which source row, in which upstream table, introduced the bad value*. This project does.

## Headline (target, pending W4 eval)

| Metric | Target |
|---|---|
| Top-1 root-cause table accuracy | ≥ 80% |
| Offending-row recall | ≥ 0.85 |
| Median time to verdict | ~ 4 min |
| Cost per incident | ~ $0.02 |

Targets are placeholders until the benchmark fills in real numbers (Week 4). See [`eval/REPORT.md`](eval/REPORT.md) for the latest run.

## Architecture (current)

```
dbt test fails
     │
     ▼
Attributor (where) — SQLGlot col-lineage → row-PK propagation
     │
     ▼
Evidence Gatherer — null/dupe/freshness/dtype probes
     │
     ▼
Classifier (what) — rules engine → LLM tiebreaker only when ambiguous
     │
     ▼
Narrator → Slack (with 👍/👎 feedback buttons)
```

Detailed component diagram + design notes: [`docs/02_revised_plan.md`](docs/02_revised_plan.md).

## Quickstart

```bash
git clone https://github.com/AntarangSharma/Ai-Powered-Data-Quality-Triangle-Agent.git
cd Ai-Powered-Data-Quality-Triangle-Agent

# 1. Install (creates .venv, pre-commit, etc.)
make install
source .venv/bin/activate

# 2. Set up env
cp .env.example .env
# edit .env and add ANTHROPIC_API_KEY

# 3. Sanity check
make test         # runs unit tests
make eval-smoke   # runs the 30-incident smoke eval (stub for now)
```

## Repo layout

```
src/dq_triage/         # the agent
  attribution/         #   Stage 1: SQLGlot walker + LLM-fallback
  classification/      #   Stage 3: rules + LLM tiebreaker
  narrator/            #   Stage 4: Slack message + one-line fix
  cache.py             #   content-hashed LLM cache
  models.py            #   Pydantic schemas (frozen, source of truth)
eval/                  # the benchmark IS the product
  faults/              #   fault injection patterns
  baselines/           #   B1–B4 baselines
  metrics.py           #   scoring functions (tested in isolation)
  runner.py            #   wires (dataset × fault × agent) → metrics
pipelines/             # the three dbt projects we eval against
docs/                  # specs, blog draft, limitations
```

## Roadmap

| Week | Deliverable |
|---|---|
| 0 | ✅ Repo scaffold, CI, models, metrics |
| 1 | Jaffle Shop + first fault class + first real eval number |
| 2 | SQLGlot Attributor + 3 more fault classes + Postgres store |
| 3 | LLM Classifier + LLM-fallback lineage + cache |
| 4 | Calibration + 4 baselines + TPC-H + full results |
| 5 | Slack UX + screencast + HuggingFace benchmark release |
| 6 | Blog + LinkedIn + launch |

## License

MIT.

## Part of the "DE Reliability Suite"

- **DQ Triage Agent** (this) — root-cause attribution after a test fails.
- *Schema Drift Detective* — catches schema changes before they break tests. (planned)
- *Self-Healing Pipeline Agent* — turns a triage verdict into a fix PR. (planned)
