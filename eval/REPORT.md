# Eval Report — full

_generated: 2026-05-21T23:32:39.225108+00:00_

## Suite

- Dataset: **jaffle_shop**
- Trials: **45**
- Predictions emitted: **45**
- Stealth faults (no test broke): **0**
- No-attribution failures: **0**
- Run dir: `eval/runs/20260521T233239Z`

## Results

| System | Top-1 table | Top-3 table | Col\|table | Row recall | Row F1 | Macro F1 | Median latency | ECE |
|---|---|---|---|---|---|---|---|---|
| SqlglotWalker (W2) | 100.0% | 100.0% | 100.0% | 1.00 | 1.00 | 1.00 | 3.5s | 0.000 |

## Per-class accuracy

| Class | Accuracy |
|---|---|
| `broken_join_dropout` | 100.0% |
| `duplicate_ingestion` | 100.0% |
| `upstream_null_spike` | 100.0% |

## Honesty disclaimer (Week 2)

What changed since W1:

- **Lineage is no longer hardcoded.** `SqlglotWalker` reads compiled
  SQL from `target/compiled/` and uses `sqlglot.lineage` to follow
  columns upstream through CTEs and across dbt-model boundaries.
- **Three fault families** (was one): `upstream_null_spike`,
  `duplicate_ingestion`, `broken_join_dropout` — 3 patterns each.
  This stresses the walker on `unique` and `relationships` tests,
  not just `not_null`.

What is **still a tautology / still untested**:

- **Classification.** The runner reads `fault.cause_class` directly
  when building each `Prediction`. So the classification F1 is the
  trivial answer — meaningful only when Week 3 replaces this with a
  rules-based classifier and the 0% classifier baseline.
- **One dataset.** Generalization across dbt projects lands in Week 4
  (TPC-H + NYC-taxi).
- **The hard case.** For `broken_join_dropout` the attributor lands
  on the *child* table (orphan FK side), not the *parent* (where the
  delete happened). That's correct attribution behaviour — telling
  null-spike from join-dropout, same blame location, is the
  classifier's job. The 100% top-1 here measures attribution, not
  root-cause identification.
