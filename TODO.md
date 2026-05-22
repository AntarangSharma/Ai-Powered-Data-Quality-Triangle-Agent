# What's left

Living checklist. Items are removed when they ship (with the commit SHA that
shipped them recorded in the git log, not here). Last updated: 2026-05-22 (afternoon).

Status legend: 🟢 in progress · 🟡 next up · 🟣 deferred (post-portfolio) · ⏸ blocked

---

## ✅ Done (shipped before this checklist existed)

The full audit is in `git log --oneline`. High-level milestones:

- **Week 1.** End-to-end loop on Jaffle Shop: dbt build → fault → attribute
  → score → REPORT.md. Models, metrics, manifest reader, CLI scaffold,
  ground-truth schema.
- **Week 2.** Replaced hardcoded lineage with a real `SqlglotWalker`
  parsing dbt-compiled SQL. Three fault families with three patterns each
  (`null_spike`, `duplicate_ingestion`, `broken_join_dropout`). Fixed
  dbt failures-table parsing for all three test kinds.
- **Week 2 (3/n).** Postgres + SQLite incident store via SQLAlchemy 2.0
  ORM + Alembic migration 0001 (`incidents`, `ground_truths`).
- **Week 3.** Rules classifier ends the W2 tautology: warehouse probes →
  `ClassifierEvidence` → three deterministic detectors. **Macro F1 = 1.00
  on 45 trials** (load-bearing now, was a tautology in W2).
- **Week 3.5.** User-facing CLI orchestrator (`dq-triage triage`) wired to
  the persistence layer. `dq-triage incidents list/show` for inspection.
- **Hygiene pass.** Repo is `ruff` + `mypy strict` + `ruff format` clean
  end-to-end; CI workflows (test / lint / eval-smoke) go green.
  README rewritten with the real 45-trial numbers, real architecture
  diagram, and a live `dq-triage triage ...` example.
- **FastAPI webhook.** Same pipeline behind `POST /webhook/dbt-failure`
  + `GET /health`. Same orchestrator function the CLI calls; OpenAPI
  schema exposes `Incident` so callers can generate typed SDKs.

---

## 🟡 Next up — finish the core agent (Week 3-4 closeout)

### LLM tiebreaker (#3 in the original plan)
- [ ] **Anthropic Haiku detector behind `Classifier(tiebreaker=…)`**. Fires
      only when top-1 < 0.7 OR top-1 − top-2 < 0.1. Today the hook exists
      but no detector is plugged in, so the path is dead code on every
      trial in the current suite (every score is 1.00).
- [ ] **Cost ceiling**: cache by `(blame_model, blame_column, evidence_hash)`
      via the existing `diskcache` so a re-run is free. Hard-cap at $0.50
      / 1000 incidents.

### Slack narrator (#4)
- [ ] **`dq_triage.narrator.slack`**: takes an `Incident`, emits a Block-Kit
      message via the existing `slack-sdk` dependency. Sonnet generates
      the narrative; Block-Kit lays it out.
- [ ] **CLI command `dq-triage notify <incident_id>`** so the same code
      path is reachable from the CLI, from a FastAPI webhook, and from an
      Airflow on-failure hook.

---

## 🟡 Generalization — Week 4

### More datasets
- [ ] **TPC-H** dbt project (10-table star schema, real joins, no
      bizarre Jaffle naming). Validates that the walker handles
      JOIN-heavy lineage, not just per-table SELECT *.
- [ ] **NYC-taxi** project (one wide table, type coercion + unit-encoding
      drift opportunities). Validates the eventual `type_coercion` /
      `unit_encoding_drift` detectors.

### More cause-class detectors
Currently 3 of 10 cause classes have rules. Each new detector is a single
function in `dq_triage.classification.rules` + 1-2 probes in
`dq_triage.stats.probes`:
- [ ] `type_coercion` (string-numeric or date-string drift)
- [ ] `late_arriving` (source freshness vs SLA)
- [ ] `source_schema_change` (column dtype changed vs last load)
- [ ] `broken_join_fanout` (row-count explosion vs parent cardinality)
- [ ] `stale_dimension` (slowly-changing-dim freshness)
- [ ] `unit_encoding_drift` (numeric mean/std shift beyond N×σ)

---

## 🟡 Hygiene & ops (Week 5 polish)

- [ ] **60-second Loom / GIF**: dbt build fails → run `dq-triage triage`
      → incident appears → `dq-triage incidents list` shows it. This is
      the single highest-leverage piece of work left for portfolio
      purposes — can't be done in this session (needs screen recording).

---

## 🟣 Deferred (good ideas, not portfolio-critical)

- Calibration of `confidence_calibrated` via held-out trials (currently
  set equal to raw `confidence`).
- Per-cause-class evidence packs for the LLM narrator (right now we hand
  it the whole `Incident` JSON).
- A second LLM (Sonnet) to write a Markdown PR comment when a dbt PR
  introduces a test failure — uses the same `Incident` payload as Slack.
- Real-warehouse adapter beyond DuckDB (Snowflake, BigQuery). The
  `SqlglotWalker` already targets a dialect; the probes don't.
- Multi-test triage: today the CLI picks one failing test. In practice
  one root cause often breaks 3-4 tests. Cluster by blame location and
  emit one incident per cluster.

---

## ⏸ Blocked / waiting

Nothing right now. The earlier "push to main" block was lifted with the
explicit "do whatever we can" authorization — the W3 + CLI work is on
GitHub as of this commit.
