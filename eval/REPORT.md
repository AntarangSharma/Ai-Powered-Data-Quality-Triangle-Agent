# Eval Report — smoke

_generated: 2026-05-22T01:04:04.083329+00:00_

## Suite

- Dataset: **jaffle_shop**
- Trials: **18**
- Predictions emitted: **18**
- Stealth faults (no test broke): **0**
- No-attribution failures: **0**
- Run dir: `eval/runs/20260522T010404Z`

## Results

| System | Top-1 table | Top-3 table | Col\|table | Row recall | Row F1 | Macro F1 | Median latency | ECE |
|---|---|---|---|---|---|---|---|---|
| SqlglotWalker + RulesClassifier (W3) | 100.0% | 100.0% | 100.0% | 1.00 | 1.00 | 1.00 | 3.1s | 0.063 |

## Per-class accuracy

| Class | Accuracy |
|---|---|
| `broken_join_dropout` | 100.0% |
| `duplicate_ingestion` | 100.0% |
| `upstream_null_spike` | 100.0% |

## Honesty disclaimer (Week 3)

What changed since W2:

- **Classification is no longer tautological.** The runner now
  builds a `ClassifierEvidence` by probing the warehouse with
  `dq_triage.stats.probes` (null rate, dupe count, orphan FK
  count) and ranks `RootCauseClass` candidates with three
  deterministic rule detectors (`dq_triage.classification.rules`).
  No LLM in the loop. The `cause_class` reported here is the
  classifier's top-1 — same number that any external user would
  see if they ran `dq-triage triage`.
- **The broken_join_dropout disambiguation works**: same blame
  location as null-spike on `raw_orders.user_id`, but the rules
  call it correctly by probing for orphan FKs against
  `raw_customers`.

What is **still untested**:

- **LLM tiebreaker.** `Classifier(tiebreaker=…)` is wired but no
  Anthropic Haiku detector is plugged in yet — runs at zero API
  cost. When classifier confidence is ≥ 0.7 with a clear top-1
  (which is every trial in the current suite), the tiebreaker
  wouldn't fire anyway.
- **Only three of ten cause classes have detectors.** The rules
  module covers `upstream_null_spike`, `duplicate_ingestion`,
  `broken_join_dropout` — the fault classes we actually inject.
  Adding `type_coercion`, `late_arriving`, etc. is a 1-detector-
  per-fault increment.
- **One dataset.** Generalization across dbt projects lands in
  Week 4 (TPC-H + NYC-taxi).
