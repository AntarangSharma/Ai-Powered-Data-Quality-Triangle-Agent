# DQ Triage Agent — Revised Plan (v1)

> Re-evaluation of `01_initial_spec.md` after a critical pass.
> This is the build plan we execute. v0 is preserved for diffing in the blog.

---

## TL;DR — what changed and why

| # | Change | Why |
|---|---|---|
| 1 | **Eval-first**: benchmark built in Week 1–2, agent iterates against it for 4 weeks | v0 hid eval until Week 4 → 3 weeks of flying blind |
| 2 | **Three datasets**: Jaffle Shop + TPC-H + NYC-taxi | Generalization story; TPC-H alone looks synthetic |
| 3 | **Public benchmark release** on HuggingFace as `dq-rca-bench-v1` | Repositions project from "agent" to "person who built the benchmark" |
| 4 | **Two-tier lineage**: SQLGlot → LLM-fallback (validated against manifest) | SQLGlot fails on ~20% of real dbt SQL; need graceful degradation |
| 5 | **Content-hashed LLM cache** from Day 1 | 1,320 LLM calls per eval → ~$1 and 5 min after first run |
| 6 | **CI eval on every PR**: 30-incident smoke + metrics-diff comment | Single biggest hireable signal in the repo |
| 7 | **Separate Attributor (where) from Classifier (what)** | Two different problems, two different evals |
| 8 | **Headline result is the ablation, not the agent** | "7 of 10 classes don't need an LLM" is the honest, memorable finding |
| 9 | **n=10 paid human study** (Fiverr/Upwork DEs, $150) | n=5 friends is too thin to publish a number |
| 10 | **Explicit kill criteria per week** | Force pivot decisions instead of grinding on dead ends |

---

## Revised architecture (the changes)

```
                ┌─────────────────────────────────────┐
                │  3 dbt projects (Jaffle/TPC-H/NYC)  │
                │  + Fault Injection Harness          │
                │  + Ground Truth Store               │
                └──────────────┬──────────────────────┘
                               │
                               ▼
┌────────────────────────────────────────────────────────────────────────┐
│                          TRIAGE AGENT                                  │
│                                                                        │
│  ┌────────────────┐                                                    │
│  │ TestFailure    │                                                    │
│  │ Listener       │                                                    │
│  └────────┬───────┘                                                    │
│           ▼                                                            │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 1 — ATTRIBUTOR ("where")                                 │   │
│  │  ├─ SQLGlot column-lineage walker (tier 1, deterministic)      │   │
│  │  ├─ LLM-fallback lineage extractor (tier 2, when SQLGlot fails)│   │
│  │  │    └─ Output validated against dbt manifest.nodes           │   │
│  │  └─ Row-PK propagation across DIRECT/JOIN/AGG/WINDOW           │   │
│  └────────────────────────┬───────────────────────────────────────┘   │
│                           ▼                                            │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 2 — EVIDENCE GATHERER                                    │   │
│  │  Stats probes (null/dupe/freshness/dtype/cardinality)          │   │
│  │  + recent loads + recent code diffs + schema snapshot          │   │
│  └────────────────────────┬───────────────────────────────────────┘   │
│                           ▼                                            │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 3 — CLASSIFIER ("what")                                  │   │
│  │  ├─ Rules engine (10 classes, deterministic)                   │   │
│  │  ├─ LLM tiebreaker (only if rules disagree or all <0.7)        │   │
│  │  └─ Isotonic-calibrated confidence                             │   │
│  └────────────────────────┬───────────────────────────────────────┘   │
│                           ▼                                            │
│  ┌────────────────────────────────────────────────────────────────┐   │
│  │ STAGE 4 — NARRATOR + NOTIFIER                                  │   │
│  │  ├─ LLM Slack-message + one-line-fix                           │   │
│  │  └─ Slack Block Kit + 👍/👎 feedback buttons → Postgres        │   │
│  └────────────────────────────────────────────────────────────────┘   │
│                                                                        │
│  Cross-cutting:                                                        │
│   - Content-hashed LLM cache (diskcache)                               │
│   - OpenTelemetry trace per incident (one trace, multi-span)           │
│   - Pydantic everywhere, frozen=True                                   │
└────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
              ┌────────────────────────────────────┐
              │ Eval Harness (the actual product)  │
              │  ├─ 220+ labeled incidents          │
              │  ├─ 4 baselines                     │
              │  ├─ Metrics suite                   │
              │  ├─ Reliability diagrams            │
              │  └─ HuggingFace dataset export      │
              └────────────────────────────────────┘
```

### Key architectural deltas vs v0

#### Delta 1 — Two-tier lineage

**Tier 1 (SQLGlot)**: parse compiled SQL, extract column edges deterministically.
**Tier 2 (LLM fallback)**: only triggers when Tier 1 returns `OPAQUE` or empty for a node. Prompt:

```
You are extracting column-level lineage from a SQL query.
The query is below. The downstream model is named {model}.
For the column {failing_column}, return a list of upstream (model, column, transform_type)
where transform_type ∈ {DIRECT,CAST,AGG,JOIN_KEY,CASE,UDF,WINDOW}.

CONSTRAINTS:
- Every upstream model MUST appear in this list: {manifest.refs}
- Every upstream column MUST exist in that model's schema (provided below).
- Return JSON matching this schema: {LineageEdge.model_json_schema()}

If you cannot determine, return [].

SQL:
{sql}
SCHEMA:
{schema}
```

Validation: drop any edge whose model isn't in the manifest. This prevents LLM hallucination by construction. Edges that pass validation get tagged `source: llm_fallback` (lower attribution_certainty).

#### Delta 2 — Attributor ↔ Classifier separation

| | Attributor | Classifier |
|---|---|---|
| Question | "Which source rows caused this?" | "What kind of bug is it?" |
| Input | failing model + column + PKs + manifest | evidence bundle |
| Output | `BlameLocation(model, column, row_pks, certainty)` | `RootCauseHypothesis(class, confidence)` |
| Eval metric | top-1/top-3 table acc + row recall | macro F1 + ECE |
| LLM? | Only on Tier-1 fallback | Only when rules disagree |

This separation matters because the project can succeed on attribution and fail on classification (or vice versa), and the blog can report them independently.

#### Delta 3 — Content-hashed LLM cache

```python
# cache.py
import hashlib, json
from diskcache import Cache
_cache = Cache(".llm_cache")

def cached_llm_call(model: str, messages: list, schema_name: str, fn):
    key = hashlib.sha256(
        json.dumps({"m": model, "msg": messages, "s": schema_name}, sort_keys=True).encode()
    ).hexdigest()
    if key in _cache:
        return _cache[key]
    result = fn()
    _cache[key] = result
    return result
```

Wrap every LLM call. Commit `.llm_cache` for eval-time determinism in CI? **No** — too large. Instead: a `cache_key_manifest.json` checked in, so anyone re-running the eval gets a hit rate report ("87% cache hits, $1.20 spent").

#### Delta 4 — CI eval workflow

`.github/workflows/eval-smoke.yml`:
- Trigger: on every PR.
- Steps: spin up DuckDB, load TPC-H SF=0.01 (tiny), run 30 fixed incidents, compute metrics, post a sticky PR comment with the diff vs main.
- Budget: ~$0.50 per run via cache; under 4 min wall clock.

`eval-full.yml`: nightly cron + manual trigger, runs the full 220 × 3 seeds, publishes `eval/REPORT_<sha>.md` as an artifact.

---

## Revised week-by-week (eval-first)

### Week 0 (this weekend, ~3 hrs) — Pre-flight
- [ ] Repo skeleton: `pyproject.toml`, `ruff`, `mypy`, `pre-commit`, `Makefile`.
- [ ] Decide branch protection: PRs require eval-smoke to pass.
- [ ] Set up `.env.example`, `.envrc` (direnv), keys for Anthropic + OpenAI.
- [ ] Empty Postgres + DuckDB containers in `docker-compose.yml`.
- [ ] Slack workspace + bot token (read-only initially).
- [ ] Open issues for every weekly DoD.

**DoD**: `make hello` runs and prints "ok" inside Docker.
**Kill criteria**: if Docker/Slack setup eats > 5 hrs, switch Slack to webhook-only and skip Compose (just use venv + Postgres on host).

---

### Week 1 — Eval harness + thin slice (10 hrs)

**Outcome**: The benchmark exists for **one fault class** (null spike), one dataset (Jaffle Shop), and the thinnest possible agent that scores on it.

**Tasks**
1. Clone `dbt-labs/jaffle_shop` (the canonical small dbt project). Get it green on DuckDB.
2. Fault injection harness skeleton: `Fault` ABC + `null_spike` fault + `GroundTruth` Pydantic model.
3. `eval/runner.py`: snapshot → mutate → dbt build → capture test failures → score.
4. **Build the metrics module FIRST** (`eval/metrics.py`): top-1 table acc, row recall, latency. Unit tested.
5. Thin agent v0.1: hardcoded one-level lineage, runs null-rate query upstream, emits `BlameLocation`.
6. Score the thin agent on 20 null-spike trials. Establish a real number.
7. `eval-smoke.yml` runs in CI on a 10-trial subset.

**DoD**:
- `make eval` produces a `REPORT.md` with real numbers.
- Thin agent gets ≥ 60% top-1 on the 20-trial null_spike set (low bar, just proving the loop works).
- CI runs the 10-trial smoke and posts to PR.

**Risks + mitigation**:
- *dbt store_failures quirks*: some test types don't store failures by default. → use `--store-failures-as=table` flag explicitly, document.
- *DuckDB version drift*: pin `duckdb==0.10.2` and `dbt-duckdb==1.8.1`.

**Kill criteria**: if by end of Week 1 you don't have **one number** in a results table, stop adding features and finish the loop. The loop is the project.

---

### Week 2 — Real Attributor (SQLGlot tier 1) + 3 more fault classes (10 hrs)

**Outcome**: Column-level lineage walker working on Jaffle Shop. Benchmark grows to 4 fault classes × 10 trials = 40 incidents.

**Tasks**
1. `attribution/sqlglot_walker.py`: BFS, edges, PK propagation. Tested on 12 canonical SQL patterns (CTE, subquery, UNION, JOIN, GROUP BY, WINDOW, CASE, CAST, COALESCE, DISTINCT, SELECT *, lateral).
2. `attribution/stats_probes.py`: parametrized SQL templates for null-rate, dupe-rate, cardinality, freshness, dtype.
3. Add 3 fault classes: `duplicate_ingestion`, `broken_join_dropout`, `type_coercion`. ≥ 3 patterns each.
4. Grow benchmark to 40 incidents. Re-run eval.
5. Pydantic models: `BlameLocation`, `LineageEdge`, `UpstreamStat`, `Incident`. `frozen=True`.
6. Postgres schema + Alembic migration 0001.
7. **Attributor-only eval**: report top-1 table acc on the 40 incidents (no classifier yet).

**DoD**:
- Attributor scores ≥ 75% top-1 table acc on 40 incidents, no LLM used.
- Row recall ≥ 0.8 on incidents that don't cross an AGG boundary.
- CI smoke still green; eval runs < 5 min cached.

**Risks + mitigation**:
- *SQLGlot edge cases*: if > 30% of Jaffle Shop models can't be walked, switch to walking the dbt `compiled.sql` (post-Jinja) which is much simpler. (This is likely.)
- *PK propagation across joins blows up*: cap intermediate row sets at 10k; sample beyond that.

**Kill criteria**: if Attributor top-1 < 50%, the SQLGlot approach isn't working — pivot to LLM-only lineage extraction for Week 3 and document why.

---

### Week 3 — Tier-2 LLM lineage + Classifier (rules) + LLM cache (10 hrs)

**Outcome**: Full pipeline end-to-end; classifier exists; LLM cache makes eval cheap.

**Tasks**
1. `attribution/llm_lineage_fallback.py` with manifest-validated outputs.
2. `cache.py` content-hashed disk cache, wraps every LLM call.
3. `classification/rules.py` — 10 deterministic class detectors. Each has its own unit test.
4. `classification/classifier.py` — aggregates scores; only calls LLM if top-1 < 0.7 OR top-2 within 0.1.
5. `classification/llm_tiebreaker.py` — `instructor`-typed, gpt-4o-mini.
6. Add remaining 6 fault classes (3 patterns each) → ~150 incidents in benchmark.
7. Full eval. **Now report both Attributor and Classifier metrics separately.**
8. Add NYC-taxi dataset (5 models, 3 fault classes, 30 trials). Re-run.

**DoD**:
- Full eval (180 incidents Jaffle + 30 NYC) runs in < 8 min cached, < $1.50 cold.
- Attributor top-1 ≥ 80% on Jaffle, ≥ 70% on NYC (generalization).
- Classifier macro F1 ≥ 0.65 with rules only.

**Risks + mitigation**:
- *LLM-fallback lineage hallucinates models that don't exist*: covered by manifest validation; log every drop.
- *Cache key collisions*: include `model_version` in the hash key.

**Kill criteria**: if NYC-taxi generalization is < 50% of Jaffle numbers, the rules are overfitting — refactor as data-driven thresholds learned from a holdout, don't just tune by hand.

---

### Week 4 — Calibration, baselines, TPC-H, paid human study (10 hrs)

**Outcome**: Full benchmark on 3 datasets; 4 baselines beaten; calibrated confidence; human study running in parallel.

**Tasks**
1. Add TPC-H SF=1 dataset (8 models, all 10 fault classes, 100 trials).
2. Implement baselines B1–B4.
3. Run benchmark × 3 seeds. Total: ~250 incidents × 3 seeds × 5 systems = 3,750 runs. With cache, ≈ $4 total.
4. Fit isotonic regression on Classifier confidence → log ECE.
5. Generate reliability diagram + confusion matrix.
6. **Hire 10 data engineers on Fiverr/Upwork** for the human study. Spec: 1 hour each, 8 incidents, $15 each = $150 total. Use Jaffle Shop only (most familiar to dbt users).
7. Compute MTTR-human and per-class accuracy.
8. `eval/REPORT.md` v1 with full results.

**DoD**:
- Agent beats B1, B2, B3 on top-1 table acc with p<0.05 (bootstrap CI).
- B4 (no-LLM ablation) gap quantified per class.
- ECE ≤ 0.08.
- Human study data in repo (anonymized), with consent log.

**Risks + mitigation**:
- *Fiverr hires flake*: post 15 jobs to get 10 completions; pre-pay.
- *Baselines accidentally beat the agent on some class*: report honestly, redesign for that class in Week 5.

**Kill criteria**: if Agent doesn't beat naive-LLM (B3) on overall top-1, the project's headline thesis is wrong — pivot blog to "what went wrong and why" instead of faking results.

---

### Week 5 — Slack UX, narrator LLM, public demo, polish (10 hrs)

**Outcome**: Live demo + Slack interactivity + screencast.

**Tasks**
1. `narrator/slack_narrator.py` — Claude Sonnet, Block Kit message, one-line fix.
2. FastAPI Slack-interactivity endpoint; 👍/👎 buttons update `Incident.human_label`.
3. OpenTelemetry: one trace per incident; export to Grafana Cloud free tier.
4. `make demo` runs a 4-minute scripted demo: inject fault → see Slack message → click confirm.
5. Public deploy to fly.io (read-only canned-incidents page; live Slack stays private).
6. 90-second Loom screencast.
7. **HuggingFace benchmark upload**: package the 250 incidents + ground truth + DuckDB snapshot as `dq-rca-bench-v1`. Write the dataset card.
8. README v1 with hero metric.

**DoD**:
- Public URL up.
- Screencast linked from README.
- Benchmark live on HuggingFace with > 0 downloads (post in DE Slack).
- All Slack feedback loops persist to DB.

**Risks + mitigation**:
- *fly.io free tier limits*: keep the public site truly static (canned data, no LLM calls from prod).
- *HuggingFace upload reviewers*: dataset cards have a checklist; follow it.

**Kill criteria**: if the demo can't run end-to-end in 4 min on a clean machine, cut features until it can.

---

### Week 6 — Blog, launch, interview prep (10 hrs)

**Outcome**: 1,500-word blog published; LinkedIn/Twitter launched; 3 STAR stories memorized.

**Tasks**
1. Write blog v1. Lead with the **ablation finding** as the surprising insight.
2. README polish + GIF + quickstart that works on a clean machine in < 5 min (test on a friend's laptop).
3. LinkedIn + Twitter thread + post in `r/dataengineering`, Locally Optimistic, dbt Slack `#show-and-tell`.
4. 3 STAR stories — written, then practiced out loud 5× each.
5. Tag `v1.0.0`. Write `CHANGELOG.md`.
6. Open `KNOWN_LIMITATIONS.md` with explicit failure modes.
7. Schedule 3 informational interviews with DE friends to pitch the project + ask what's weak.
8. Update LinkedIn headline to mention the benchmark.

**DoD**:
- Blog published with at least 3 outbound comments engaging with content.
- LinkedIn post > 500 impressions [ASSUMPTION: depends on network size].
- 3 informational interviews scheduled.

**Kill criteria**: if the blog draft after 4 hrs of writing isn't tight, ship a shorter 800-word version focused only on the ablation result. Better short and sharp than long and meandering.

---

## Pre-flight checklist (do these before Week 1)

- [ ] Anthropic API key with $20 prepaid credit
- [ ] OpenAI API key with $20 prepaid credit
- [ ] Slack workspace (use a personal one, not your employer's)
- [ ] HuggingFace account
- [ ] fly.io account
- [ ] GitHub repo created, public from Day 1 ("build in public" beats "reveal at end")
- [ ] Twitter/X account warmed (post 2–3 "what I'm building" tweets in Week 0)
- [ ] Decide: pseudonym or real name on the repo? (Real name; this is a job-hunt artifact.)

## Repo layout we'll create

```
dq-triage/
├── pyproject.toml                # uv-managed; Python 3.11
├── Makefile                      # make {install, fmt, lint, test, eval, demo}
├── docker-compose.yml            # postgres, duckdb (file-mounted), grafana
├── .github/workflows/
│   ├── eval-smoke.yml            # 30-incident eval on every PR
│   ├── eval-full.yml             # nightly + manual
│   └── lint.yml
├── docs/
│   ├── 01_initial_spec.md        # ← already saved
│   ├── 02_revised_plan.md        # ← this file
│   ├── ARCHITECTURE.md           # generated diagrams
│   ├── KNOWN_LIMITATIONS.md
│   └── blog/post.md
├── pipelines/                    # 3 dbt projects
│   ├── jaffle_shop/
│   ├── tpch/
│   └── nyc_taxi/
├── src/dq_triage/
│   ├── __init__.py
│   ├── models.py                 # Pydantic schemas
│   ├── attribution/
│   │   ├── sqlglot_walker.py
│   │   ├── llm_lineage_fallback.py
│   │   └── stats_probes.py
│   ├── classification/
│   │   ├── rules.py
│   │   ├── classifier.py
│   │   └── llm_tiebreaker.py
│   ├── narrator/
│   │   └── slack_narrator.py
│   ├── store/
│   │   ├── alembic/
│   │   └── repository.py
│   ├── cache.py
│   └── cli.py                    # typer
├── eval/
│   ├── faults/                   # 22 fault patterns
│   ├── ground_truth.py
│   ├── runner.py
│   ├── metrics.py
│   ├── baselines/
│   │   ├── b1_test_name.py
│   │   ├── b2_elementary.py
│   │   ├── b3_naive_llm.py
│   │   └── b4_no_llm.py
│   ├── human_study/
│   └── REPORT.md
├── benchmark_export/             # HuggingFace dataset payload
└── tests/
    ├── unit/
    └── integration/
```

## Why this plan is more accurate (the meta-argument)

1. **You can't beat baselines you haven't built.** v0 wrote prose about baselines; v1 builds them by Week 4 from a benchmark that's already running.
2. **Three datasets >> 220 trials on one.** Reviewers' first question is "does it generalize?" Answer it pre-emptively.
3. **The cache turns eval from a $30 monthly cost into a $1 one-time cost.** This is the difference between "I ran it once" and "I run it on every PR".
4. **Public benchmark = compounding credibility.** The repo gets downloads, citations, GitHub stars on its own merit, separately from the agent.
5. **Headline result = the ablation.** Honest, surprising, defensible in interviews. "I built it and discovered the LLM was only essential for 30% of cases" is a senior-IC story.
6. **Kill criteria force pivots before Week 5.** The most common portfolio failure mode is grinding on a broken approach for two weeks too long.

## What we build first (next session)

When you say "go", we start in this exact order:

1. **`Week 0` pre-flight commands** — repo scaffolding, Docker compose, pinned deps.
2. **`eval/metrics.py` + tests** — write the scoring functions before the agent.
3. **`eval/faults/null_spike.py`** — first fault class.
4. **`pipelines/jaffle_shop/`** — clone, get it green on DuckDB.
5. **`eval/runner.py`** — wire fault → dbt build → score loop.
6. **First number in `REPORT.md`** — even if it's "0%, agent doesn't exist yet".

Then we add the agent and watch the number climb.

---

## Open decisions I need from you before we start coding

1. **Real name on the repo?** (Recommend: yes.)
2. **GitHub username/handle to use?**
3. **Budget cap for paid human study?** ($150 recommended; OK with $0 → drop the human-vs-agent comparison and use only agent metrics.)
4. **Anthropic + OpenAI keys** ready? If not, we can mock LLM calls for Weeks 1–2 and add real calls in Week 3.
5. **Hours/week** — confirming 10 hrs/week × 6 weeks. If less, we cut NYC-taxi + the public HF release first.
6. **Public from Day 1 or private until Week 5?** (Recommend public — "build in public" tweets cost nothing and the GitHub history becomes a story.)
