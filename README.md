# DQ Triage Agent

> When a dbt test fails, this agent walks **column-level lineage upstream**, finds the **exact source rows** that caused the failure, classifies the root cause, and emits a structured incident in seconds.

**Not an anomaly detector. Root-cause attribution with row-level precision.**

[![tests](https://github.com/AntarangSharma/Ai-Powered-Data-Quality-Triangle-Agent/actions/workflows/test.yml/badge.svg)](https://github.com/AntarangSharma/Ai-Powered-Data-Quality-Triangle-Agent/actions/workflows/test.yml)
[![lint](https://github.com/AntarangSharma/Ai-Powered-Data-Quality-Triangle-Agent/actions/workflows/lint.yml/badge.svg)](https://github.com/AntarangSharma/Ai-Powered-Data-Quality-Triangle-Agent/actions/workflows/lint.yml)
[![python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## Headline numbers (45-trial Jaffle Shop benchmark)

| Metric | Value |
|---|---|
| **Top-1 root-cause table accuracy** | **100%** (45 / 45) |
| **Offending-row recall** | **1.00** |
| **Macro F1 over cause classes** | **1.00** |
| **Median time to verdict** | **2.56 s** |
| **API cost per incident** | **$0.00** (deterministic rules — no LLM in the loop yet) |

Three fault families × three patterns each × five seeds = 45 trials.
Each trial: seed a clean Jaffle warehouse → inject a fault → run `dbt build` → triage.
Reproduce with `make eval-full`. Latest run: [`eval/REPORT.md`](eval/REPORT.md).

> The numbers are load-bearing, not target placeholders. **Macro F1 reaches 1.00 because the rules classifier disambiguates `broken_join_dropout` from `upstream_null_spike` even when both faults land on the same blame column (`raw_orders.user_id`)** — it probes orphan FK counts against the parent table.

---

## Why this exists

Current OSS data-quality tools (Elementary, re_data, dbt tests alone) stop at **detection**: they tell you *that* something failed. They don't tell you *which source row, in which upstream table, introduced the bad value*.

A typical real-world incident path today:

```
dbt test fails  →  someone gets paged  →  they read the test name  →
they SELECT * the failures table  →  they manually trace the column
upstream  →  20 minutes later they find the deleted parent row.
```

This agent collapses the middle three steps into a single function call. Median latency on the current benchmark is **2.56 seconds**.

---

## How it works

```
                          ┌────────────────────────┐
   dbt build fails ──────►│ load_failing_tests     │  ◄── reads target/run_results.json
                          └─────────┬──────────────┘      and target/manifest.json
                                    │
                                    ▼
                          ┌────────────────────────┐
                          │ SqlglotWalker          │  ◄── parses dbt-compiled SQL,
                          │   .attribute()         │      walks column lineage upstream,
                          └─────────┬──────────────┘      propagates row PKs hop-by-hop
                                    │  BlameLocation(model, column, row_pks)
                                    ▼
                          ┌────────────────────────┐
                          │ assemble_evidence      │  ◄── probes the warehouse:
                          │                        │      null_rate, pk_dupe_count,
                          └─────────┬──────────────┘      orphan_fk_count, row_count
                                    │  ClassifierEvidence
                                    ▼
                          ┌────────────────────────┐
                          │ Classifier             │  ◄── 3 deterministic detectors
                          │   .classify()          │      ranked by score; LLM
                          └─────────┬──────────────┘      tiebreaker only fires when
                                    │  ClassScore[]       top-1 < 0.7 (not in suite yet)
                                    ▼
                          ┌────────────────────────┐
                          │ Incident               │  ◄── frozen Pydantic, persisted
                          │   (Postgres / SQLite)  │      via SQLAlchemy + Alembic
                          └────────────────────────┘
```

Every box is a real module under [`src/dq_triage/`](src/dq_triage/). Every arrow has a unit test.

---

## Quickstart

```bash
git clone https://github.com/AntarangSharma/Ai-Powered-Data-Quality-Triangle-Agent.git
cd Ai-Powered-Data-Quality-Triangle-Agent
make install          # creates .venv, installs deps, runs pre-commit
source .venv/bin/activate
```

### Run the benchmark (no API key needed)

```bash
make eval-smoke       # 18 trials, ~1 minute
make eval-full        # 45 trials, ~3 minutes  →  eval/REPORT.md
```

### Triage a real dbt failure

```bash
# 1. (in your dbt project) Let dbt run and fail naturally
cd path/to/your/dbt/project
dbt build                          # exits non-zero, writes target/run_results.json

# 2. Triage the failure
dq-triage triage \
    --project   path/to/your/dbt/project \
    --duckdb    path/to/warehouse.duckdb

# 3. Inspect persisted incidents
dq-triage incidents list
dq-triage incidents show inc_<id>
```

Output:

```
───────────────────── inc_114de60254a2 ─────────────────────
Failing test: relationships_stg_orders_customer_id__...  (stg_orders.customer_id)
Failing rows: 12   Latency: 0.37s   Verdict: auto
Blame: raw_orders.user_id  (certainty 1.00, 12 row(s))
Cause: broken_join_dropout  (confidence 1.00)
Why:   12 orphan FK value(s) in blame column have no matching parent row.
Fix:   Restore deleted parent rows referenced by raw_orders.user_id, or soft-delete.
```

---

## What's implemented vs. what's planned

See [`TODO.md`](TODO.md) for the full living checklist.

**Implemented and load-bearing:**
- `SqlglotWalker` — real column-lineage walking over parsed dbt-compiled SQL.
- `dq_triage.stats.probes` — null rate, dupe count, orphan FK count, row count, column stats.
- `dq_triage.agent.evidence.assemble_evidence` — defensive warehouse probes → `ClassifierEvidence`.
- `dq_triage.classification` — three deterministic detectors (`upstream_null_spike`, `duplicate_ingestion`, `broken_join_dropout`) with calibrated thresholds + tiebreaker seam.
- `dq_triage.agent.orchestrator.triage` — full pipeline as one call. Returns a frozen `Incident`.
- `dq_triage.store` — Postgres (prod) + SQLite (dev) via SQLAlchemy 2.0 + Alembic. Lossless Pydantic round-trip.
- CLI: `dq-triage triage`, `dq-triage incidents {list,show}`.
- 127 unit + integration tests. mypy strict clean. ruff lint+format clean.

**Planned (see [`TODO.md`](TODO.md)):**
- Anthropic Haiku tiebreaker when classifier confidence is low.
- Slack Block-Kit narrator (Claude Sonnet).
- TPC-H + NYC-taxi datasets to prove generalization.
- Seven more cause-class detectors (`type_coercion`, `late_arriving`, etc.).
- Loom / GIF demo recording.

---

## Repo layout

```
src/dq_triage/
  agent/
    evidence.py        # warehouse probes → ClassifierEvidence
    orchestrator.py    # the triage() entry point
  attribution/
    sqlglot_walker.py  # column-lineage walker
    manifest.py        # dbt manifest reader
  classification/
    rules.py           # 3 deterministic detectors
    classifier.py      # ranking + tiebreaker hook
  stats/probes.py      # null/dupe/orphan-FK probes (SQL-safe)
  store/               # SQLAlchemy ORM + Alembic migrations
  cli.py               # typer CLI: triage / incidents
  models.py            # Pydantic schemas (frozen, source of truth)

eval/
  faults/              # 9 fault patterns (null_spike, dupe, broken_join)
  ground_truth.py      # how injected faults map to expected blame
  metrics.py           # top-1 accuracy, row recall, macro F1, ECE
  runner.py            # builds the suite, runs trials, writes REPORT.md

pipelines/jaffle_shop/ # the dbt project the benchmark runs against
migrations/            # Alembic schema history
tests/unit/            # 127 tests
docs/                  # specs + design notes
TODO.md                # what's left
```

---

## Design choices worth noting

- **Pydantic everywhere on the wire.** Every model is `frozen=True`. No `Any` in public fields. Enums for closed sets — never raw strings.
- **Deterministic by default.** The classifier is rules-only today, so re-running an incident gives bit-identical output. The LLM tiebreaker is wired but inert until budget permits.
- **dbt's failures tables are not friendly.** `not_null` stores full rows; `unique` stores `(unique_field, n_records)`; `relationships` stores only `from_field` (the orphan FK value). The runner and orchestrator both handle all three shapes.
- **The benchmark is the product.** `eval/REPORT.md` is the regression test — if a refactor drops Macro F1 below 1.00, the PR doesn't ship.
- **Same code path for CLI / FastAPI / Airflow.** `triage()` is the single entry point; the CLI is a 10-line wrapper.

---

## License

MIT. See [`LICENSE`](LICENSE).

---

## Part of the "DE Reliability Suite"

- **DQ Triage Agent** (this) — root-cause attribution after a test fails.
- *Schema Drift Detective* — catches schema changes before they break tests. (planned)
- *Self-Healing Pipeline Agent* — turns a triage verdict into a fix PR. (planned)
