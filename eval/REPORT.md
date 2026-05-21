# Eval Report — smoke

_generated: 2026-05-21T22:43:26.461949+00:00_

## Suite

- Dataset: **jaffle_shop**
- Trials: **9**
- Predictions emitted: **9**
- Stealth faults (no test broke): **0**
- No-attribution failures: **0**
- Run dir: `eval/runs/20260521T224326Z`

## Results

| System | Top-1 table | Top-3 table | Col\|table | Row recall | Row F1 | Macro F1 | Median latency | ECE |
|---|---|---|---|---|---|---|---|---|
| ThinAttributor (W1) | 100.0% | 100.0% | 100.0% | 1.00 | 1.00 | 1.00 | 4.1s | 0.000 |

## Per-class accuracy

| Class | Accuracy |
|---|---|
| `upstream_null_spike` | 100.0% |

## ⚠️ Honesty disclaimer (Week 1)

These numbers are intentionally easy. The Week-1 thin slice has:

- **One fault class** (`upstream_null_spike`) — and the runner hardcodes
  the predicted class to match. So the classification F1 is a tautology
  until Week 3 lands the rules-based classifier.
- **One fault target** (`raw_orders.user_id`) — the same column in every
  trial. Generalization is untested.
- **Hardcoded lineage map** — `ThinAttributor` knows by hand that
  `stg_orders.customer_id` comes from `raw_orders.user_id`. The real
  SQLGlot walker lands Week 2 and *must* match these numbers without
  the hardcoding to count as progress.

The point of Week 1 is to **close the loop end-to-end**:
fault → dbt → failing PKs → attribute → score. The number being 100%
tells us the loop works. It does NOT tell us the agent is good.
