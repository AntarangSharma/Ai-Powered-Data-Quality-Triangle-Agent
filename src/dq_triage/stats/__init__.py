"""Statistical probes — the small SQL queries the agent (and classifier) runs
against the warehouse to gather evidence about a table or column.

These are the building blocks the Week-3 classifier feeds into its rules.
Today they're used by the Week-2 attribution layer for upstream defect
verification."""

from dq_triage.stats.adapter import (
    BigQueryAdapter,
    DuckDBAdapter,
    PostgresAdapter,
    SnowflakeAdapter,
    WarehouseAdapter,
)
from dq_triage.stats.probes import (
    ColumnStats,
    DupeKey,
    FreshnessReading,
    probe_column_dtype,
    probe_column_stats,
    probe_dupe_count,
    probe_freshness,
    probe_row_count,
)

__all__ = [
    "BigQueryAdapter",
    "ColumnStats",
    "DuckDBAdapter",
    "DupeKey",
    "FreshnessReading",
    "PostgresAdapter",
    "SnowflakeAdapter",
    "WarehouseAdapter",
    "probe_column_dtype",
    "probe_column_stats",
    "probe_dupe_count",
    "probe_freshness",
    "probe_row_count",
]
