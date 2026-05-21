# Eval Report — smoke

_generated: 2026-05-21T23:01:49.094912+00:00_

## Suite

- Dataset: **jaffle_shop**
- Trials: **9**
- Predictions emitted: **9**
- Stealth faults (no test broke): **0**
- No-attribution failures: **0**
- Run dir: `eval/runs/20260521T230149Z`

## Results

| System | Top-1 table | Top-3 table | Col\|table | Row recall | Row F1 | Macro F1 | Median latency | ECE |
|---|---|---|---|---|---|---|---|---|
| SqlglotWalker (W2) | 100.0% | 100.0% | 100.0% | 1.00 | 1.00 | 1.00 | 2.9s | 0.000 |

## Per-class accuracy

| Class | Accuracy |
|---|---|
| `upstream_null_spike` | 100.0% |

## ⚠️ Honesty disclaimer (Week 2)

These numbers are still easy. What has and has NOT changed since W1:

- **Lineage is no longer hardcoded.** `SqlglotWalker` reads compiled SQL
  from `target/compiled/` and uses `sqlglot.lineage` to follow columns
  upstream through CTEs and across dbt-model boundaries. The same
  100% / 1.0 numbers now reflect real parsing, not a lookup table.
- **One fault class** (`upstream_null_spike`) — the runner still
  hardcodes the predicted class. Classification F1 remains a tautology
  until Week 3 lands the rules-based classifier.
- **One fault target** (`raw_orders.user_id`). Generalization across
  columns and dbt projects is still untested; more fault classes and a
  ~40-incident benchmark land later in Week 2.

The point of these reports is to **prove the loop works end-to-end**
and to track delta as we replace components. 100% on the W1 suite is a
necessary condition, not a sufficient one.
