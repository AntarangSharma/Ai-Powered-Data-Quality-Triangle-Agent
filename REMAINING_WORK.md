# Remaining Work — Path to 100% Complete

> Comprehensive checklist for taking this project from its current state
> (~70% of the original v1 plan, fully functional core agent) to **100%
> of the v1 scope as defined in [`docs/02_revised_plan.md`](docs/02_revised_plan.md)**.
>
> Each item lists: scope, why it matters, effort estimate, dependencies, and
> acceptance criteria. Items are ordered by **leverage** — what moves the
> project most toward "shippable as a portfolio piece + open-source release"
> per hour invested.
>
> Last updated: 2026-05-22.

---

## Current completeness scorecard

| Pillar | Status | Notes |
|---|---|---|
| Core pipeline (attribute → probe → classify → persist) | **100%** | 136 tests, Macro F1 = 1.00 on 45 trials |
| Cause-class coverage | **30%** | 3 of 10 detectors written |
| Dataset coverage | **33%** | 1 of 3 (Jaffle Shop only) |
| LLM integration | **0%** | Tiebreaker hook + cache wired but inert |
| Notification surface | **0%** | Slack narrator not built |
| Calibration | **0%** | `confidence_calibrated == confidence` |
| Baselines | **0%** | 0 of 4 baselines implemented |
| Public benchmark release | **0%** | Not packaged for HuggingFace |
| Launch artifacts (blog, demo) | **0%** | Not written / recorded |
| Operational polish | **80%** | CI green, README done, API + CLI working |

**Overall: ~50% of the v1 plan, but 100% of the "working agent" subset.**
The remaining 50% is generalization (more datasets, more detectors, more
LLM integration) + launch artifacts (blog, benchmark release, paid study).

---

## Tier 1 — Ship-blockers (must-have for 100%)

These items are explicit deliverables in `docs/02_revised_plan.md` and are
**required to call the project complete as originally scoped**.

### 1.1 — Anthropic Haiku LLM tiebreaker (Stage 3, tier 2)

**Scope.** Implement `dq_triage.classification.haiku_tiebreaker` that
takes a `ClassifierEvidence` + the top-3 `ClassScore`s and returns a
refined ranking. Wire it into `Classifier(tiebreaker=...)`.

**Why.** The classification stage was designed as two tiers: deterministic
rules (done) + LLM tiebreaker (not done). Without tier 2, ambiguous cases
fall back to the lower-confidence rule pick. The current Jaffle suite has
zero ambiguous cases (every score is 1.00), but TPC-H and NYC-taxi will.

**Effort.** ~3 hours. ~$0.05 in API credits for a full smoke eval (cached
after first run via `diskcache`).

**Dependencies.** `ANTHROPIC_API_KEY` env var, existing `dq_triage.cache`.

**Acceptance.**
- [ ] `Classifier(tiebreaker=haiku_tiebreaker)` fires only when top-1 < 0.7
      OR (top-1 minus top-2) < 0.1.
- [ ] Cache key = `sha256(evidence.canonical_json() + top_3_class_names)`.
      Hit ratio ≥ 95% on re-runs.
- [ ] Hard cost cap: refuse to call API if `os.environ.get("DQ_LLM_BUDGET_USD")`
      would be exceeded. Default: $0.50 / 1000 incidents.
- [ ] Unit tests: 3 (mocked LLM response, cache hit path, budget cap path).
- [ ] Eval re-run shows zero net change in numbers (Jaffle has no
      ambiguous cases) — proves the tiebreaker is correctly gated off.

---

### 1.2 — Slack narrator (Stage 4, the user-visible surface)

**Scope.** Two new modules:
1. `dq_triage.narrator.composer` — Claude Sonnet call that turns an
   `Incident` into a 3-paragraph narrative (what, where, suggested fix).
2. `dq_triage.narrator.slack` — Block-Kit layout + `slack-sdk` posting.

**Why.** Without a notification surface, the agent's output stays inside
the warehouse. The "Slack thread with one-line fix" promise in the
original spec is the demo-able feature; everything else is plumbing.

**Effort.** ~5 hours. ~$0.10 / 1000 incidents in API credits (Sonnet is
~3x Haiku). Cached aggressively.

**Dependencies.** Slack workspace + bot token (free tier OK), 1.1's cache.

**Acceptance.**
- [ ] `narrator.composer.compose(incident: Incident) -> NarratedIncident`
      returns a frozen Pydantic with `headline`, `narrative`, `one_line_fix`.
- [ ] `narrator.slack.post(narrated: NarratedIncident, channel: str)` posts
      via `slack-sdk`, returns the thread `ts` for follow-up replies.
- [ ] Block-Kit layout: header (verdict colour-coded), section (narrative),
      action buttons (👍 / 👎 — write feedback to `incidents.human_label`).
- [ ] CLI: `dq-triage notify <incident_id> --channel <name>`.
- [ ] FastAPI endpoint: `POST /webhook/dbt-failure?notify=slack:<channel>`.
- [ ] Unit tests: 5 (composer with mocked LLM, slack posting with mocked
      `slack-sdk`, Block-Kit shape, feedback button → DB roundtrip,
      duplicate-post idempotency).

---

### 1.3 — Seven additional cause-class detectors

**Scope.** Bring `dq_triage.classification.rules` from 3/10 to 10/10. Each
detector is a single function that returns `ClassScore | None`. Each may
need 1-2 new probes in `dq_triage.stats.probes` and a matching fault
pattern in `eval/faults/` to be testable.

**Why.** The original spec promised "10 root-cause classes". Shipping 3
is honest but not complete. The detectors closest to the existing probes
(and therefore cheapest to add):

| Class | Probe needed | Fault pattern needed | Est. hours |
|---|---|---|---|
| `late_arriving` | `probe_freshness` (exists!) | `eval/faults/late_arriving.py` | 1 |
| `type_coercion` | `probe_dtype_distribution` (new) | `eval/faults/type_coercion.py` | 2 |
| `source_schema_change` | `probe_column_dtype` (exists!) + dtype history | dtype-flip fault | 2 |
| `broken_join_fanout` | `probe_join_cardinality` (new) | `eval/faults/broken_join_fanout.py` | 2 |
| `unit_encoding_drift` | `probe_column_stats` (exists!) + z-score history | numeric-drift fault | 2 |
| `stale_dimension` | `probe_freshness` + ratio to fact freshness | stale-dim fault | 1.5 |
| `upstream_value_skew` | `probe_column_stats` (exists!) | distribution-shift fault | 1.5 |

**Effort.** ~12 hours total for all seven.

**Dependencies.** Each requires a corresponding fault pattern so the
eval can verify the detector fires; otherwise it's dead-tested.

**Acceptance.**
- [ ] 10 of 10 detectors implemented (`rules.py` exports them all).
- [ ] Each detector has ≥ 2 unit tests (fires / doesn't fire).
- [ ] Eval suite extended to include all 10 fault families (3 patterns
      each, 5 seeds) = 150 trials. Macro F1 ≥ 0.85 (lower than 1.00 is
      expected because some classes are inherently ambiguous).
- [ ] `eval/REPORT.md` updated with per-class confusion matrix.

---

### 1.4 — TPC-H dataset

**Scope.** Add `pipelines/tpch_dbt/` (a real 10-table star schema dbt
project pointing at the canonical TPC-H tables generated into DuckDB).
Add `eval/datasets/tpch.py` (DatasetConfig + FaultTargets for each of
the 10 cause classes).

**Why.** Jaffle Shop is 3 tables with toy SQL. TPC-H is the standard
benchmark for warehouse workloads: 10 tables, real JOINs, real
aggregations. Proves the `SqlglotWalker` works on non-trivial lineage.

**Effort.** ~6 hours (mostly the dbt models — fault adapters reuse the
existing `_NullSpikeBase` etc.).

**Dependencies.** `duckdb-tpch` extension OR a TPC-H generator + seed files.

**Acceptance.**
- [ ] `pipelines/tpch_dbt/` has `dbt_project.yml`, sources for 10 raw
      tables, 5+ staging models, 3+ mart models with multi-table JOINs.
- [ ] All 10 fault families have a TPC-H FaultTarget (e.g. `null_spike`
      on `lineitem.l_partkey`, `broken_join_dropout` on `supplier.s_suppkey`).
- [ ] `make eval-tpch-smoke` runs 30 trials in < 5 minutes.
- [ ] Eval numbers on TPC-H reported in `eval/REPORT.md` alongside Jaffle.

---

### 1.5 — NYC-taxi dataset

**Scope.** Same as TPC-H but for the NYC taxi parquet (one wide table,
heavy on numeric / datetime / categorical columns). Specifically targets
`type_coercion` and `unit_encoding_drift` which are awkward on the other
two datasets.

**Effort.** ~4 hours (single table → simpler dbt project).

**Dependencies.** ~500 MB of parquet (link in spec, no auth needed).

**Acceptance.**
- [ ] `pipelines/nyc_taxi_dbt/` exists with at least 1 raw + 2 staging
      + 1 mart model.
- [ ] FaultTargets defined for all 10 cause classes.
- [ ] Eval included in `make eval-full`.

---

### 1.6 — Isotonic calibration of `confidence_calibrated`

**Scope.** Replace today's `confidence_calibrated = confidence` (line in
`orchestrator.py`) with a `sklearn.isotonic.IsotonicRegression` fitted on
held-out eval trials. Persist the fit to a file alongside the package.

**Why.** Raw rule scores are not calibrated probabilities — a `0.8` from
the dupe detector and a `0.8` from the null detector mean different things.
Calibration is what makes the Verdict thresholds (AUTO @ 0.85 etc.)
meaningful. The original spec calls this out explicitly.

**Effort.** ~3 hours.

**Dependencies.** A held-out split of the eval (which we don't have yet —
currently every trial is used for the F1 number). Need to introduce
`eval/split.py` with train/calib/test = 60/20/20.

**Acceptance.**
- [ ] `dq_triage.classification.calibration.fit(pairs) -> IsotonicCalibrator`
- [ ] Calibrator persisted as `src/dq_triage/classification/calib.joblib`.
- [ ] Expected Calibration Error (ECE) computed in `eval/metrics.py`
      (already implemented! just wire it up) and reported in REPORT.md.
- [ ] ECE on test split ≤ 0.05.

---

### 1.7 — Four baselines (the ablation table)

**Scope.** Implement four straw-man competitors and run them through the
same eval harness:

1. **B1 — `dbt-test-only`**: emits the test name and column. No attribution.
2. **B2 — `random-blame`**: picks a random raw table as blame. Lower bound.
3. **B3 — `LLM-only`**: feeds the failing rows directly to Claude and
   asks for cause + blame. No walker, no probes, no rules.
4. **B4 — `single-LLM-call`**: same as agent but classifier is one Sonnet
   call instead of rules+tiebreaker. Tests whether rules add value.

**Why.** The original headline result is supposed to be **the ablation**,
not the agent's absolute numbers: "Rules + targeted LLM beats Pure LLM by
X% at Y% the cost". Without baselines, the F1 = 1.00 number reads as
either cherry-picked or trivial — neither is true, but the eval doesn't
prove it.

**Effort.** ~6 hours.

**Dependencies.** 1.1 (LLM tiebreaker plumbing), so the LLM baselines can
reuse the cache.

**Acceptance.**
- [ ] `eval/baselines/` contains all 4 baselines, each exposing the same
      `predict(trial) -> Prediction` interface as the agent.
- [ ] `eval/runner.py --baseline {b1,b2,b3,b4,agent}` selects which to run.
- [ ] REPORT.md includes a comparison table (5 rows × 4 metrics) on
      identical 150-trial splits across all 3 datasets.

---

### 1.8 — CI eval workflow (smoke on every PR)

**Scope.** Add `.github/workflows/eval-pr.yml` that runs `make eval-smoke`
on every PR and posts a comment with the metrics diff vs `main`.

**Why.** The original spec calls this out as **the single biggest hireable
signal in the repo**. A reviewer can see, in 60 seconds, whether a PR
moved the F1 number up or down. We already have the eval; we just need
the GH Actions glue.

**Effort.** ~3 hours.

**Dependencies.** The existing `eval-smoke.yml` runs the smoke eval but
doesn't post a comment. Need: post-eval step that reads `eval/REPORT.md`,
diffs against the version on `main`, and posts via `actions/github-script`.

**Acceptance.**
- [ ] PR comment shows: `Top-1 acc: 100% → 100% (no change)`,
      `Row recall: 1.00 → 1.00 (no change)`, etc.
- [ ] Diff is colour-coded (red on regression, green on improvement).
- [ ] Eval-smoke completes in < 3 minutes on a free-tier runner.

---

## Tier 2 — Strongly recommended (90% → 100% polish)

### 2.1 — Public benchmark release on HuggingFace as `dq-rca-bench-v1`

**Scope.** Package the fault-injection harness + ground-truth dataset as
a downloadable benchmark on HuggingFace Datasets. Includes:
- The 3 dbt projects.
- The 150-trial labeled ground truth (CSV + JSONL).
- A `bench.py` script that lets anyone run their own agent against it.
- A leaderboard README in the dataset card.

**Why.** Original spec says this **repositions the project from "person who
built an agent" to "person who built the benchmark"** — much more durable
portfolio signal.

**Effort.** ~6 hours.

**Dependencies.** All Tier 1 done (so the ground truth is comprehensive).

**Acceptance.**
- [ ] Dataset uploaded to `huggingface.co/datasets/AntarangSharma/dq-rca-bench-v1`.
- [ ] README explains the schema, the metrics, and how to submit.
- [ ] At least the agent's own numbers are on the leaderboard.

---

### 2.2 — 60-second demo recording (Loom / GIF)

**Scope.** Screen recording showing:
1. `dbt build` failing.
2. `dq-triage triage --project … --duckdb …` showing the live verdict.
3. `dq-triage incidents list` showing the persisted record.

**Why.** README links are nice; a video is what gets shared on LinkedIn.

**Effort.** ~1 hour (recording + light edit).

**Dependencies.** None.

**Acceptance.**
- [ ] MP4 (or GIF) under 10 MB committed to `docs/demo.gif`.
- [ ] Embedded in the README at the top.

---

### 2.3 — Blog post + LinkedIn launch thread

**Scope.** Two pieces:
1. ~2000-word blog post on the **honest finding**: "I built an LLM-heavy
   agent and then proved 7 of 10 root-cause classes don't need an LLM".
2. ~5-tweet LinkedIn version with the headline chart.

**Why.** The original Week 6 deliverable. The blog is what turns a repo
into a public artifact.

**Effort.** ~6 hours.

**Dependencies.** 1.7 (baselines) — without the ablation, there's no thesis.

**Acceptance.**
- [ ] Blog drafted in `docs/blog_draft.md`, published to personal blog
      or Medium / dev.to.
- [ ] LinkedIn post drafted in `docs/launch_thread.md`.

---

### 2.4 — Paid n=10 human study

**Scope.** Recruit 10 mid-level data engineers on Fiverr/Upwork ($15
each, $150 total). Show them a sample of 5 failing tests + the agent's
verdicts. Ask: (a) do you trust the verdict, (b) how long would manual
triage take, (c) any catastrophic misses?

**Why.** Original spec calls this out as the difference between "n=5
friends" (too thin to publish) and a defensible UX claim.

**Effort.** ~3 hours of your time (recruiting, screening, debrief).

**Dependencies.** 2.2 (demo so they understand what they're evaluating).

**Acceptance.**
- [ ] 10 completed surveys with at least 7/10 saying they'd use it.
- [ ] Anonymised raw data in `docs/human_study/`.
- [ ] Headline number ("DEs estimate 4x faster MTTR with the agent") in
      the README and blog.

---

### 2.5 — Multi-test triage clustering

**Scope.** Today the CLI/API triages **one** failing test. In practice,
one root cause often breaks 3-4 tests simultaneously (e.g. dropping a
parent row breaks the relationships test AND the downstream row-count test).
Cluster failing tests by blame location and emit ONE incident per cluster.

**Why.** Without this, a single root cause produces 4 separate Slack
threads — the opposite of the "stop the page storm" pitch.

**Effort.** ~4 hours.

**Dependencies.** None.

**Acceptance.**
- [ ] `dq_triage.agent.cluster.cluster_failures(failing: list[FailingTest])
      -> list[FailureCluster]` groups by `(blame_model, blame_column)`.
- [ ] Orchestrator emits one Incident per cluster, with all member
      failing tests listed in `Incident.related_failures`.
- [ ] Schema migration `0002_failure_cluster.py`.

---

### 2.6 — OpenTelemetry tracing per incident

**Scope.** Wrap each pipeline stage (attribute / probe / classify /
narrate) in an OTEL span. Export to whatever the user has configured
(stdout in dev, OTLP in prod).

**Why.** Original spec calls this out as cross-cutting. Without it,
debugging a slow incident in production means reading logs.

**Effort.** ~2 hours.

**Dependencies.** `opentelemetry-api` + `opentelemetry-sdk` in deps.

**Acceptance.**
- [ ] Each `triage()` call produces one trace, ~5 spans.
- [ ] `OTEL_EXPORTER_OTLP_ENDPOINT` env var routes them externally.
- [ ] Unit test asserts span hierarchy.

---

## Tier 3 — Stretch (100% → ambitious)

These exceed the original v1 plan but turn the project from "complete
portfolio piece" into "open-source product".

### 3.1 — Snowflake / BigQuery / Postgres warehouse adapters

**Scope.** Today the probes assume DuckDB. Generalize to other backends
by wrapping every probe in a `WarehouseAdapter` protocol with
backend-specific quoting + dtype handling.

**Effort.** ~10 hours per backend.

**Acceptance.** Same eval suite passes on a Postgres-backed dbt project.

---

### 3.2 — GitHub PR narrator

**Scope.** When the failing test was introduced by a recent PR, post the
narrative as a PR comment (instead of just Slack). Uses `gh api` /
`PyGitHub`.

**Effort.** ~3 hours.

**Acceptance.** `dq-triage notify <incident_id> --github-pr 42` posts.

---

### 3.3 — Self-healing PR generator (the next agent in the suite)

**Scope.** Take a triaged Incident and generate a fix PR (e.g. for
`broken_join_dropout`, write a `WHERE NOT EXISTS` filter or a backfill
SQL script). This is the **Self-Healing Pipeline Agent** mentioned at
the bottom of the README — it's a separate project, not a checkbox here.

**Effort.** ~40 hours (a whole second project).

**Acceptance.** N/A this repo.

---

### 3.4 — Hosted demo (Cloud Run + public link)

**Scope.** Deploy the FastAPI app to Cloud Run. Stand up a public Jaffle
warehouse so anyone can hit `POST /webhook/dbt-failure` from `curl` and
see a live triage.

**Effort.** ~4 hours.

**Dependencies.** GCP account; estimated cost ~$3 / month idle.

**Acceptance.**
- [ ] `https://dq-triage-demo.run.app/webhook/dbt-failure` returns a real
      Incident.
- [ ] README has a `curl` example pointing at the hosted URL.

---

### 3.5 — OpenAPI-generated TypeScript SDK

**Scope.** `openapi-typescript-codegen` against the running API, publish
as `@antarangsharma/dq-triage-sdk` on npm. Lets node-based pipelines
(e.g. Dagster) integrate without ad-hoc HTTP.

**Effort.** ~2 hours.

---

## Tier 4 — Known papercuts (no tier, no urgency)

Small things spotted in the code that aren't bugs but aren't great either:

- [ ] `Incident.failing_rows_sample` is always `()` today (the
      orchestrator doesn't populate it). The Pydantic model accepts up
      to 5 rows — should sample from the failures table.
- [ ] `Incident.upstream_stats` is always `()` — needs a historical
      stats store (rolling 30-day null rate, etc.). Currently we probe
      "today" only.
- [ ] `pyproject.toml` ships ~30 dependencies; many (e.g. `instructor`,
      `polars`) are unused after the W3 simplification. Audit and prune.
- [ ] The `_DEFAULT_PK_MAP` in the orchestrator is Jaffle-specific.
      Promote to a `.dq_triage_config.yml` per-project file.
- [ ] `dbt_runner.build()` does `run` then `test` in two subprocesses;
      could be `dbt build` (single command). Won't change correctness,
      saves ~0.5s per trial.
- [ ] `test_cli_orchestrator.py::test_failing_test_dataclass_is_frozen`
      uses `pytest.raises(AttributeError)` — Python 3.11+ raises
      `FrozenInstanceError` (subclass of `AttributeError` for dataclass
      frozen). Should specify the concrete exception for clarity.
- [ ] `KNOWN_LIMITATIONS.md` mentions things that have since been fixed.
      Audit + prune.

---

## How to get to 100%, ordered by ROI

If I had to do this in one focused weekend, here's the order:

1. **2.2 (demo recording)** — 1h, makes the README sellable.
2. **1.1 (LLM tiebreaker)** — 3h, unblocks the rest of the LLM work.
3. **1.2 (Slack narrator)** — 5h, the user-visible feature people remember.
4. **1.7 (4 baselines)** — 6h, gives the thesis.
5. **1.6 (calibration)** — 3h, makes the verdict thresholds honest.
6. **1.3 (7 detectors)** — 12h, completes the cause-class promise.
7. **1.4 + 1.5 (TPC-H + NYC-taxi)** — 10h, generalization story.
8. **1.6 (CI eval comments)** — 3h, hireable signal.
9. **2.1 (HuggingFace release)** — 6h, repositions the project.
10. **2.3 (blog + LinkedIn)** — 6h, makes it public.

**Total: ~55 hours.** That's a focused two weekends (or a single
sustained week if cleared of other work) to hit 100%.

---

## Definition of "100% complete"

The project is 100% complete when **all of the following** are true:

- ✅ All 10 cause-class detectors are implemented and exercised by the eval.
- ✅ All 3 datasets (Jaffle, TPC-H, NYC-taxi) are in the eval suite.
- ✅ The LLM tiebreaker + Slack narrator are wired and tested live.
- ✅ All 4 baselines are implemented and the ablation is in REPORT.md.
- ✅ `confidence_calibrated` is real (isotonic, ECE ≤ 0.05).
- ✅ CI runs the smoke eval on every PR and comments the diff.
- ✅ `dq-rca-bench-v1` is live on HuggingFace.
- ✅ A demo video is embedded in the README.
- ✅ A blog post is published.
- ✅ The n=10 paid human study is run and the number is in the README.

**Where this stops short of "perfect":** the Tier-3 items (Snowflake,
hosted demo, SDK, Self-Healing Pipeline Agent) are explicitly **out of
scope of v1** and live in a hypothetical v2.
