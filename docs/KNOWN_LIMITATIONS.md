# Known Limitations

Living document. Things this project will **not** handle in v1.0, by design.

## Lineage

- **dbt macros that compile to runtime-dynamic SQL** (e.g., `dbt_utils.pivot` with dynamic column lists) are walked using the post-compile SQL. If the compiled SQL still contains opaque expressions, the LLM-fallback extractor takes over; we tag those edges `source: llm_fallback` with reduced certainty.
- **UDFs** are opaque. Edges crossing a UDF stop with `transform_type=OPAQUE`.
- **Aggregations break row identity.** When the walk crosses an `AGG` boundary we switch from row-level to group-level blame and say so in the Slack message.

## Coverage

- **dbt only.** No Great Expectations adapter in v1. Architecture supports it; we just don't ship one yet.
- **DuckDB only for eval.** Snowflake/BigQuery adapter stubs exist for shape but are not in the benchmark.
- **English-only narrator.**

## Evaluation

- **Synthetic faults.** All ground truth comes from controlled mutations. We don't yet have a real-world failure dataset.
- **No paid human study in v1.0** (budget = $0). We report absolute MTTR, not vs-human speedup.
- **n=3 seeds.** Statistically thin tail; bootstrap CIs are reported alongside point estimates.

## Operational

- **No multi-tenant.** Single project, single Slack workspace, single Postgres.
- **No PII redaction.** Sample rows in Slack are shown as-is. Do not deploy on production data without adding redaction.

## Honesty about the LLM

- For 5 of the 10 root-cause classes (late_arriving, null_spike, duplicate_ingestion, broken_join_*, schema_change), the rules engine should be within 5pts of the full agent. The LLM mainly earns its keep on type_coercion, unit_drift, and ambiguous/multi-cause incidents.
- This is documented and reported as a headline result, not a footnote.
