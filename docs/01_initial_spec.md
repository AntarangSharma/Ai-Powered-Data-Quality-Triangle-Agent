# DQ Triage Agent — Initial Spec (v0)

> Original 4-deliverable spec produced before the re-evaluation pass.
> See `02_revised_plan.md` for the updated build strategy.

---

## Scope reshape (3 bullets)

- **Drop the GE adapter for v1.** dbt tests already give you `store_failures` (a table of failing PKs) which is the cleanest hook for row-level attribution. Supporting GE doubles your surface area for ~10% more credibility. Add it in Week 7 if time permits.
- **Restrict v1 to DuckDB + a synthetic multi-stage pipeline.** Snowflake/BigQuery adapters are a 30-min interface swap if your lineage layer is SQLGlot-based; building them now adds infra cost and zero eval signal. Ship the adapter *stub* + a documented `IWarehouseAdapter` protocol, that's enough to be hireable.
- **The "agent" is mostly deterministic.** ~80% of root-cause attribution is SQL + column lineage + stats. The LLM does: (a) taxonomy classification from a structured evidence bundle, (b) Slack-ready summary, (c) one-line fix suggestion. Be explicit about this in the blog — it's a *credibility signal*, not a limitation.

---

## DELIVERABLE 1 — ARCHITECTURE

### Component diagram (text)

```
                            ┌──────────────────────────────────┐
                            │  dbt Core 1.8 (test runner)      │
                            │  + store_failures: ON            │
                            └──────────────┬───────────────────┘
                                           │ on_run_end hook
                                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                       TRIAGE AGENT (Python 3.11, FastAPI)               │
│                                                                         │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ TestFailure  │→ │ EvidenceGatherer │→ │ LineageWalker (SQLGlot)  │  │
│  │ Listener     │  │ (SQL + DuckDB)   │  │ column-level, BFS        │  │
│  └──────────────┘  └──────────────────┘  └──────────┬───────────────┘  │
│                                                     ▼                  │
│                                          ┌──────────────────────────┐  │
│                                          │ RootCauseClassifier      │  │
│                                          │ (rules → LLM tiebreaker) │  │
│                                          └──────────┬───────────────┘  │
│                                                     ▼                  │
│                                          ┌──────────────────────────┐  │
│                                          │ IncidentStore (Postgres) │  │
│                                          │ + Slack Notifier         │  │
│                                          └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
       │                                                  │
       ▼                                                  ▼
  ┌─────────────┐                              ┌───────────────────┐
  │ OpenLineage │ (events from dbt → Marquez)  │ Slack workspace   │
  │ Marquez UI  │  for table-level cross-check │ #dq-incidents     │
  └─────────────┘                              └───────────────────┘

  Warehouse: DuckDB 0.10 (local) | Postgres 16 (incident store)
  Orchestrator: GitHub Actions (cron + on-push) | Prefect 2 [ASSUMPTION: local-only ok]
```

### Stack with one-line justification

| Layer | Choice | Why (one line) |
|---|---|---|
| Test framework | **dbt-core 1.8 + `store_failures`** | Gives you a physical table of failing PKs per test — the only clean row-level hook in the OSS ecosystem. |
| Lineage (column-level) | **SQLGlot 23.x** | Deterministic AST-based column lineage; no LLM needed; works on any SQL dialect dbt compiles to. |
| Lineage (table-level, cross-check) | **OpenLineage + Marquez 0.45** | Industry-standard event spec; recruiters recognize it; useful for source-system lineage outside dbt. |
| Warehouse (dev) | **DuckDB 0.10** | Free, fast, runs in CI, ANSI-ish SQL, no infra. |
| Warehouse (adapter stub) | **Snowflake via `snowflake-connector-python` 3.7** | One file, proves it composes. Not wired into eval. |
| Incident store | **Postgres 16 + SQLAlchemy 2 + Alembic** | Boring, hireable, transactional. |
| Agent framework | **Plain Python + Pydantic 2.7** | An "agent framework" here is overkill — flow is bounded DAG, not free-form ReAct. Be explicit in the blog. |
| LLM | **Anthropic Claude Sonnet 4** (primary) + **OpenAI gpt-4o-mini** (cheap classifier) | Sonnet for hypothesis ranking + Slack summary; mini for structured taxonomy classification at $0.15/1M in. |
| LLM client | **`instructor` 1.x on top of Anthropic SDK** | Pydantic-typed outputs, retries on schema fail. |
| Slack | **`slack-sdk` 3.27 + Block Kit** | Threads, buttons for `/confirm`, `/wrong-guess`. |
| Orchestration | **GitHub Actions** (eval runs) + **Prefect 2** local (live demo) | GHA for reproducible benchmark; Prefect for the "live" feel. |
| Observability | **OpenTelemetry → Grafana Cloud free tier** | Trace each incident end-to-end; screenshot for README. |
| Deployment | **Docker Compose** + **fly.io** for demo | Cheap, public URL, one command. |
| Eval | **`pytest` + custom `eval_harness` package** | Same tooling devs already trust. |

### Root-cause taxonomy (10 classes)

| # | Class | Detection heuristic | Evidence gathered | Confidence rubric (0–1) |
|---|---|---|---|---|
| 1 | **Late-arriving data** | Failing rows have `loaded_at` within last freshness SLA window AND upstream source max(`event_time`) < expected | Source freshness lag, last N load timestamps, partition counts | 0.95 if freshness lag > 2× p95; 0.7 if 1–2×; else 0.4 |
| 2 | **Upstream null spike** | `null_rate(col)` in upstream source for last load > 3σ vs trailing 30 loads | Null-rate timeseries, schema, last commit touching transform | 0.9 if z > 5; 0.7 if 3–5; degrade if other classes also fire |
| 3 | **Type coercion / silent cast** | SQLGlot detects implicit cast in lineage path AND failing rows have values at type boundary | Column dtype chain, sample boundary values, compiled SQL diff | 0.85 if explicit `CAST` found in path; 0.6 if implicit |
| 4 | **Duplicate ingestion** | `count(*) group by natural_key` > 1 in source; failing test is `unique` | Dupe groups, ingestion job IDs, file hashes if available | 0.95 if exact-row dupes; 0.75 if near-dupes |
| 5 | **Broken join (fan-out)** | Row count of failing model > expected; join key cardinality changed upstream | Row counts per model in path, cardinality of each join key trailing 14d | 0.9 if cardinality jump > 2×; 0.6 otherwise |
| 6 | **Broken join (drop-out)** | Failing test is `not_null` or `relationships`; upstream FK has values not in dim table | Set diff: `source.fk - dim.pk` (top 20), recent dim refreshes | 0.9 if missing FKs cluster on recent values; 0.7 otherwise |
| 7 | **Source-system schema change** | Information schema diff vs snapshot from last successful run | Column add/drop/rename, last DDL event, OpenLineage facet | 0.95 if column literally missing/renamed |
| 8 | **Stale dimension / SCD2 bug** | Failing fact rows reference dim keys with `valid_to < event_time` | Dim refresh log, % rows pointing to expired versions | 0.85 if > 5% rows expired; 0.6 if 1–5% |
| 9 | **Unit / encoding drift** | Distribution of numeric col shifted by ~constant factor (cents↔dollars, m↔km); or encoding column shows non-UTF8 rate spike | Mean/std/min/max trailing 30d vs current, top-10 char encodings | 0.7 if shift is clean ~10×/100×/1000×; 0.5 otherwise — flag for human |
| 10 | **Unknown / multi-cause** | No single class > 0.7 OR top 2 within 0.1 | Full evidence bundle, top-3 candidates | Always < 0.6 — triggers human-in-loop |

> Classes 1, 4, 5, 6, 7 are ~entirely deterministic. The LLM's job for these is *narrative + Slack formatting*, not detection.

### Upstream-walk algorithm

```
INPUT:
  failing_model: str
  failing_column: str
  failing_pks: list[Any]

ALGORITHM (column-level BFS, max_depth=6):
  1. Parse compiled SQL for failing_model with SQLGlot.
  2. Extract column lineage edges: (downstream_col) -> [(upstream_model, upstream_col, transform_type)]
     where transform_type ∈ {DIRECT, CAST, AGG, JOIN_KEY, CASE, UDF, WINDOW}.
  3. Push (failing_model, failing_column, failing_pks) onto queue.
  4. While queue and depth < max_depth:
       a. Pop (model, col, pks).
       b. For each upstream (u_model, u_col, transform):
            - If transform == DIRECT or CAST:
                u_pks = pks                            # row identity preserved
                attribution_certainty *= 1.0 (DIRECT) or 0.95 (CAST)
            - If transform == JOIN_KEY:
                u_pks = SELECT u_pk FROM u_model
                        WHERE u.join_key IN (failing rows' join_key values)
                attribution_certainty *= 0.85
            - If transform == AGG:
                u_pks = rows in the failing groups
                STOP further col-level walk for that branch; mark AGG_BOUNDARY
                attribution_certainty *= 0.7
            - If transform == UDF or unsupported:
                STOP this branch, record OPAQUE
            - If transform == WINDOW:
                u_pks = rows in same partition as failing rows
                attribution_certainty *= 0.8
       c. Run stats probes on (u_model, u_col, u_pks)
       d. Push (u_model, u_col, u_pks) if attribution_certainty > 0.5.

STOP CRITERIA (any):
  - Reached a source table.
  - attribution_certainty drops below 0.5.
  - Hit AGG_BOUNDARY on all branches.
  - Hit max_depth.
  - Found a node where ALL failing pks correspond to rows that:
      * already violate an invariant in that node (null/dupe/type), AND
      * the immediate-downstream node does NOT independently violate it.
    → This is the "blame node".

OUTPUT:
  blame_node: (model, column, attribution_certainty)
  walk_path: list of nodes visited
  evidence_per_node: dict of stats probes
```

### Evidence bundle (token-budgeted)

```yaml
incident_id: INC-2026-0421-007
failing_test:
  name: not_null_fct_orders_customer_id
  model: marts.fct_orders
  column: customer_id
  failure_count: 7
  failing_rows_sample:
    - {order_id: 88421, customer_id: null, order_ts: "2026-05-20T03:14:00Z"}
lineage_walk:
  blame_node: {model: staging.stg_orders_raw, column: cust_id, certainty: 0.91}
  path:
    - {model: marts.fct_orders,        column: customer_id, transform: DIRECT}
    - {model: marts.int_orders_joined, column: customer_id, transform: JOIN_KEY}
    - {model: staging.stg_orders_raw,  column: cust_id,     transform: DIRECT}
upstream_stats:
  staging.stg_orders_raw.cust_id:
    null_rate_today: 0.041
    null_rate_trailing_30d_p50: 0.0001
    null_rate_z_score: 8.7
    dtype: VARCHAR
    last_loaded_at: "2026-05-21T01:02:00Z"
    rows_in_last_load: 142018
recent_loads: [...]
recent_code_changes: [...]
source_freshness:
  raw.orders: {lag_minutes: 14, sla_minutes: 60}
deterministic_classifier_output:
  top_3:
    - {class: upstream_null_spike, score: 0.91}
    - {class: source_system_change, score: 0.42}
    - {class: late_arriving, score: 0.08}
```

### Confidence + human-in-loop

- `confidence ≥ 0.85` AND single top class → **Auto-post verdict** with confirm/wrong buttons.
- `0.6 ≤ confidence < 0.85` OR top-2 within 0.1 → **Two-candidate post**.
- `confidence < 0.6` → **Triage-only post**.

**Calibration**: bin into 10 confidence buckets, fit isotonic regression, report ECE.

### Pydantic schemas

```python
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from pydantic import BaseModel, Field

class RootCauseClass(str, Enum):
    LATE_ARRIVING       = "late_arriving"
    UPSTREAM_NULL_SPIKE = "upstream_null_spike"
    TYPE_COERCION       = "type_coercion"
    DUPLICATE_INGESTION = "duplicate_ingestion"
    BROKEN_JOIN_FANOUT  = "broken_join_fanout"
    BROKEN_JOIN_DROPOUT = "broken_join_dropout"
    SOURCE_SCHEMA_CHANGE = "source_schema_change"
    STALE_DIMENSION     = "stale_dimension"
    UNIT_ENCODING_DRIFT = "unit_encoding_drift"
    UNKNOWN             = "unknown"

class LineageEdge(BaseModel):
    downstream_model: str
    downstream_column: str
    upstream_model: str
    upstream_column: str
    transform_type: Literal["DIRECT","CAST","AGG","JOIN_KEY","CASE","UDF","WINDOW"]
    attribution_certainty: float = Field(ge=0, le=1)

class UpstreamStat(BaseModel):
    model: str
    column: str
    null_rate_today: float
    null_rate_p50_30d: float
    null_rate_z: float
    distinct_count: int
    dtype: str
    last_loaded_at: datetime
    anomaly_score: float = Field(ge=0, le=1)

class RootCauseHypothesis(BaseModel):
    cause_class: RootCauseClass
    blame_model: str
    blame_column: str | None
    blame_rows_sample: list[dict[str, Any]] = Field(max_length=5)
    confidence: float = Field(ge=0, le=1)
    confidence_calibrated: float = Field(ge=0, le=1)
    evidence_summary: str = Field(max_length=600)
    suggested_one_line_fix: str = Field(max_length=240)

class Incident(BaseModel):
    incident_id: str
    created_at: datetime
    failing_test_name: str
    failing_model: str
    failing_column: str | None
    failing_row_count: int
    failing_rows_sample: list[dict[str, Any]] = Field(max_length=5)
    lineage_path: list[LineageEdge]
    upstream_stats: list[UpstreamStat]
    hypotheses: list[RootCauseHypothesis] = Field(min_length=1, max_length=3)
    final_verdict: RootCauseHypothesis | None
    human_label: RootCauseClass | None = None
    time_to_verdict_seconds: float
    token_cost_usd: float
```

### Cost model

Per incident:
- **Deterministic stage**: 0 LLM tokens. ~2–4s wall clock on DuckDB.
- **Classifier call** (gpt-4o-mini): ~3.5k input, ~250 output → ~$0.00068.
- **Slack-summary** (Claude Sonnet 4): ~5k input, ~400 output → ~$0.021.
- **Total: ~$0.022 / incident.**

At 50 incidents/day in a real org: ~$33/month. Headline: **"~2¢ per incident, ~4 min from failure to verdict."**

---

## DELIVERABLE 2 — EVAL DATASET & METHODOLOGY

### Seeded pipeline
- **Domain**: TPC-H scale factor 1 (≈6M rows, 8 tables). NYC-taxi as secondary in Week 6.
- **Stages**: `raw` → `staging` → `intermediate` → `marts`. ~22 dbt models, ~60 dbt tests.

### Fault injection harness
```
faults/
  base.py
  late_arrival.py
  null_spike.py
  type_coercion.py
  duplicate_ingestion.py
  broken_join_fanout.py
  broken_join_dropout.py
  schema_change.py
  stale_scd2.py
  unit_drift.py
  multi_cause.py
```

Loop: snapshot → sample fault → mutate raw → reload DuckDB → `dbt build` → run agent → score vs ground truth → persist.

Target: **220 trials** = 22 patterns × 10 trials. 90/10 train/test split.

### Metrics
| Metric | Formula |
|---|---|
| Top-1 table acc | `mean(1[P.blame_model == G.source_table])` |
| Top-3 table acc | `mean(1[G.source_table ∈ P.candidates[:3]])` |
| Column acc \| table correct | `mean(1[P.blame_column == G.source_column] given table correct)` |
| Offending-row recall | `|P.pks ∩ G.pks| / |G.pks|` |
| Offending-row precision | `|P.pks ∩ G.pks| / |P.pks|` |
| Class F1 (macro) | sklearn `f1_score(macro)` |
| MTTR-agent | mean wall clock |
| MTTR speedup | `MTTR_human / MTTR_agent` |
| ECE | `Σ_b (n_b/N) × |acc(b) - conf(b)|` |
| $/incident | from API usage |
| Hallucination rate | `mean(1[P.blame_model ∉ manifest.nodes])` |

### Baselines
1. **B1** — Test-name guess.
2. **B2** — Elementary OSS anomaly tool.
3. **B3** — Naive single-LLM call.
4. **B4** — Agent ablation (no LLM, deterministic only).

### Human baseline timing study
N=5 participants, 10 incidents each, IID. Self-timed via wrapper script. 30-min cap. Report median, p90, accuracy per class.

### Contamination + split
- Hold out 20 incidents (10%) for final reporting.
- Re-run final eval 3× with different LLM seeds.

### Results table template
| Metric | B1 | B2 | B3 | B4 | **Agent** |
|---|---|---|---|---|---|
| Top-1 table acc | __ | __ | __ | __ | __ |
| Top-3 table acc | __ | __ | __ | __ | __ |
| Column acc \| table | __ | __ | __ | __ | __ |
| Offending-row recall | __ | __ | __ | __ | __ |
| Class F1 (macro) | __ | __ | __ | __ | __ |
| MTTR (sec) | __ | __ | __ | __ | __ |
| MTTR vs human | __× | __× | __× | __× | __× |
| ECE | n/a | n/a | __ | __ | __ |
| $/incident | $0 | $0 | $__ | $0 | $__ |

Target [ASSUMPTION]: Agent ≥ **78% top-1 table**, ≥ **92% top-3**, ≥ **0.85 row recall**, **MTTR ≈ 4 min vs human median ≈ 25 min**.

---

## DELIVERABLE 3 — WEEK-BY-WEEK BUILD PLAN (6 weeks, ~10 hrs/week)

### Week 1 — Thin slice end-to-end
**Outcome**: Failing dbt test → 1-level walk → Slack message with sample rows.
**Tasks**: repo init; dbt+DuckDB+TPC-H; `on_run_end` hook; listener; hardcoded lineage; one stats query; Slack post.
**DoD**: `dbt build` → Slack in < 60s, screenshot in README.
**Risk**: Slack OAuth friction. **Mitigation**: webhook first.
**Cut**: Block Kit niceties.

### Week 2 — Real column-level lineage + 3 root-cause classes
**Outcome**: SQLGlot walker; deterministic detection for null_spike, dupe, late_arriving.
**Tasks**: `sqlglot_walker.py`; BFS w/ stop criteria; stats probes module; 3 classifier rules; Pydantic models; Postgres + Alembic; SQLGlot edge tests; first 30-trial seed run.
**DoD**: ≥ 80% top-1 on these 3 classes.
**Risk**: SQLGlot fails on macros. **Mitigation**: walk compiled SQL only.
**Cut**: skip `relationships` test support.

### Week 3 — LLM tier + remaining classes + first benchmark pass
**Outcome**: Full 10-class taxonomy, instructor-typed LLM calls, 100-trial run.
**Tasks**: remaining fault patterns; remaining detectors; evidence bundle serializer; gpt-4o-mini classifier; Claude Sonnet narrator; confidence aggregation; 100-trial run; fix top-3 failure modes.
**DoD**: 100 trials in < 30 min; ≥ 70% top-1; cost ≤ $3.
**Risk**: LLM JSON schema failures. **Mitigation**: instructor retries; deterministic fallback.
**Cut**: Skip multi-cause class.

### Week 4 — Eval rigor: 220 trials + baselines + calibration
**Outcome**: Reportable results vs all 4 baselines; calibrated confidence.
**Tasks**: 22 fault patterns; B1–B4 baselines; 220 × 3 seed runs; isotonic regression on confidence; full metrics; confusion matrix; `eval/REPORT.md`.
**DoD**: Agent strictly beats B1/B2/B3 on top-1 and row recall; honest about classes B4 ties.
**Risk**: Loses to B3. **Mitigation**: This *is* the result — write it honestly.
**Cut**: drop calibration if crunched.

### Week 5 — Polish: human study, Slack UX, observability, demo
**Outcome**: Demoable system + human baseline data + feedback buttons.
**Tasks**: 5-person human study; Block Kit message; button → FastAPI → DB; OTel + Grafana; Docker Compose; fly.io demo; 90s screencast; iterate on 3 worst classes.
**DoD**: Public URL live, screencast in README, human data in repo.
**Risk**: fly.io limits, Slack OAuth distribution. **Mitigation**: read-only public demo, private Slack workspace.
**Cut**: skip OTel — structured logs suffice.

### Week 6 — Generalization, narrative, launch
**Outcome**: NYC-taxi rerun, blog draft, README done, launch posts scheduled.
**Tasks**: 30-trial NYC-taxi run; update results; 1500w blog; polish README; 3 STAR stories; LinkedIn + Twitter; tag v1.0.0; submit to community.
**DoD**: v1.0.0 tagged; blog published; one inbound comment.
**Risk**: Blog overruns. **Mitigation**: Outline in W5.
**Cut**: Skip NYC-taxi if behind.

---

## DELIVERABLE 4 — README + BLOG + LAUNCH NARRATIVE

### README skeleton
Hero metric (median 4 min vs 25 min), GIF placeholder, architecture diagram, 60s how-it-works, results table, limitations, quickstart, DE Reliability Suite cross-link.

### Blog candidate titles
1. **"The 2 a.m. Slack ping: building a data-quality agent that does the 90 minutes of detective work for you"** ← best
2. "Column-level lineage is the missing primitive in modern data quality"
3. "Anomaly detection is the easy half: a benchmark for data-quality root-cause attribution"

### LinkedIn post + 5-tweet thread + 3 STAR stories
*(See `04_launch_assets.md` once W6 lands.)*

---

## Closing positioning note

In interviews, lead with the *benchmark*, not the *agent*. "I built a 220-incident root-cause-attribution benchmark and an agent that beats four baselines on it" beats "I built an AI agent for data quality" every time.
