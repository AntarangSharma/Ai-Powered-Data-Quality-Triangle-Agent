"""Warehouse adapters to decouple stats probes from specific databases.

Supports DuckDB, Postgres, and stubs for Snowflake/BigQuery.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from dq_triage.stats.probes import ColumnStats, DupeKey, FreshnessReading


class WarehouseAdapter(Protocol):
    """Protocol defining statistical query capabilities for any database backend."""

    def probe_row_count(self, table: str) -> int:
        """Total row count of the relation."""
        ...

    def probe_column_dtype(self, table: str, column: str) -> str:
        """SQL data type of the column."""
        ...

    def probe_column_stats(self, table: str, column: str) -> ColumnStats:
        """Return basic column stats: row count, null count, distinct count, dtype."""
        ...

    def probe_dupe_count(
        self, table: str, key_columns: tuple[str, ...], limit: int = 50
    ) -> tuple[DupeKey, ...]:
        """Find values of key_columns that appear more than once."""
        ...

    def probe_freshness(
        self, table: str, timestamp_column: str, observed_at: datetime
    ) -> FreshnessReading:
        """Get max timestamp in column and how recent it is."""
        ...

    def probe_dtype_distribution(self, table: str, column: str) -> tuple[float, float]:
        """Compute VARCHAR integer/float compatibility ratios."""
        ...

    def probe_numeric_moments(self, table: str, column: str) -> tuple[float, float]:
        """Compute mean and standard deviation of a numeric column."""
        ...

    def probe_skew(self, table: str, column: str) -> float:
        """Compute skewness of a numeric column."""
        ...


class DuckDBAdapter:
    """WarehouseAdapter for DuckDB using direct connection."""

    def __init__(self, con: Any) -> None:
        self.con = con

    def probe_row_count(self, table: str) -> int:
        from dq_triage.stats.probes import probe_row_count

        return probe_row_count(self.con, table)

    def probe_column_dtype(self, table: str, column: str) -> str:
        from dq_triage.stats.probes import probe_column_dtype

        return probe_column_dtype(self.con, table, column)

    def probe_column_stats(self, table: str, column: str) -> ColumnStats:
        from dq_triage.stats.probes import probe_column_stats

        return probe_column_stats(self.con, table, column)

    def probe_dupe_count(
        self, table: str, key_columns: tuple[str, ...], limit: int = 50
    ) -> tuple[DupeKey, ...]:
        from dq_triage.stats.probes import probe_dupe_count

        return probe_dupe_count(self.con, table, key_columns, limit)

    def probe_freshness(
        self, table: str, timestamp_column: str, observed_at: datetime
    ) -> FreshnessReading:
        from dq_triage.stats.probes import probe_freshness

        return probe_freshness(self.con, table, timestamp_column, observed_at)

    def probe_dtype_distribution(self, table: str, column: str) -> tuple[float, float]:
        from dq_triage.stats.probes import probe_dtype_distribution

        return probe_dtype_distribution(self.con, table, column)

    def probe_numeric_moments(self, table: str, column: str) -> tuple[float, float]:
        from dq_triage.stats.probes import probe_numeric_moments

        return probe_numeric_moments(self.con, table, column)

    def probe_skew(self, table: str, column: str) -> float:
        from dq_triage.stats.probes import probe_skew

        return probe_skew(self.con, table, column)


class PostgresAdapter:
    """WarehouseAdapter for PostgreSQL using an DB-API or SQLAlchemy connection."""

    def __init__(self, con: Any) -> None:
        self.con = con

    def _execute(self, sql: str, params: list | None = None) -> Any:
        # Standard execute for DB-API/SQLAlchemy connections
        if hasattr(self.con, "execute"):
            if params:
                return self.con.execute(sql, params)
            return self.con.execute(sql)
        elif hasattr(self.con, "cursor"):
            cur = self.con.cursor()
            cur.execute(sql, params or ())
            return cur
        raise ValueError("Unsupported connection type for PostgresAdapter")

    def probe_row_count(self, table: str) -> int:
        from dq_triage.stats.probes import _quote_relation

        sql = f"SELECT COUNT(*) FROM {_quote_relation(table)}"
        res = self._execute(sql).fetchone()
        return int(res[0]) if res else 0

    def probe_column_dtype(self, table: str, column: str) -> str:

        parts = table.split(".")
        if len(parts) == 1:
            schema_clause = "table_schema = 'public' AND "
            params = [parts[0], column]
        elif len(parts) == 2:
            schema_clause = "table_schema = %s AND "
            params = [parts[0], parts[1], column]
        else:
            schema_clause = "table_schema = %s AND "
            params = [parts[1], parts[2], column]

        sql = (
            "SELECT data_type FROM information_schema.columns "
            f"WHERE {schema_clause}table_name = %s AND column_name = %s"
        )
        res = self._execute(sql, params).fetchone()
        if not res:
            raise LookupError(f"Column not found: {table}.{column}")
        return str(res[0])

    def probe_column_stats(self, table: str, column: str) -> ColumnStats:
        from dq_triage.stats.probes import ColumnStats, _quote_ident, _quote_relation

        rel = _quote_relation(table)
        col = _quote_ident(column)
        sql = (
            f"SELECT COUNT(*), "
            f"       SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END), "
            f"       COUNT(DISTINCT {col}) "
            f"FROM {rel}"
        )
        row = self._execute(sql).fetchone()
        row_count, null_count, distinct_count = row if row else (0, 0, 0)
        dtype = self.probe_column_dtype(table, column)
        return ColumnStats(
            table=table,
            column=column,
            row_count=int(row_count),
            null_count=int(null_count or 0),
            distinct_count=int(distinct_count),
            dtype=dtype,
        )

    def probe_dupe_count(
        self, table: str, key_columns: tuple[str, ...], limit: int = 50
    ) -> tuple[DupeKey, ...]:
        from dq_triage.stats.probes import DupeKey, _quote_ident, _quote_relation

        if not key_columns:
            raise ValueError("key_columns must be non-empty")
        cols_sql = ", ".join(_quote_ident(c) for c in key_columns)
        rel = _quote_relation(table)
        key_concat = " || '|' || ".join(f"CAST({_quote_ident(c)} AS VARCHAR)" for c in key_columns)
        sql = (
            f"SELECT ({key_concat}) AS k, COUNT(*) AS c "
            f"FROM {rel} "
            f"GROUP BY {cols_sql} "
            f"HAVING COUNT(*) > 1 "
            f"ORDER BY c DESC "
            f"LIMIT {int(limit)}"
        )
        rows = self._execute(sql).fetchall()
        return tuple(DupeKey(value=str(r[0]), count=int(r[1])) for r in rows)

    def probe_freshness(
        self, table: str, timestamp_column: str, observed_at: datetime
    ) -> FreshnessReading:
        from dq_triage.stats.probes import FreshnessReading, _quote_ident, _quote_relation

        rel = _quote_relation(table)
        col = _quote_ident(timestamp_column)
        sql = f"SELECT MAX({col}) FROM {rel}"
        row = self._execute(sql).fetchone()
        max_ts = None if not row or row[0] is None else row[0]
        # Coerce date/datetime
        if max_ts and not isinstance(max_ts, datetime):
            from datetime import date

            if isinstance(max_ts, date):
                max_ts = datetime(max_ts.year, max_ts.month, max_ts.day)
            else:
                max_ts = datetime.fromisoformat(str(max_ts))
        return FreshnessReading(
            table=table,
            timestamp_column=timestamp_column,
            max_timestamp=max_ts,
            observed_at=observed_at,
        )

    def probe_dtype_distribution(self, table: str, column: str) -> tuple[float, float]:
        # Postgres try_cast approximation or float casting check
        from dq_triage.stats.probes import _quote_ident, _quote_relation

        dtype = self.probe_column_dtype(table, column).upper()
        if (
            "VARCHAR" not in dtype
            and "TEXT" not in dtype
            and "STRING" not in dtype
            and "CHAR" not in dtype
        ):
            return 0.0, 0.0
        rel = _quote_relation(table)
        col = _quote_ident(column)

        # In Postgres, TRY_CAST can be done with custom functions, or a regex mismatch check.
        # Integer: matches only digits and optional sign
        # Float: matches standard floating point
        sql = (
            f"SELECT COUNT(*), "
            f"       SUM(CASE WHEN {col} ~ '^[+-]?[0-9]+$' THEN 1 ELSE 0 END), "
            f"       SUM(CASE WHEN {col} ~ '^[+-]?[0-9]*\\.?[0-9]+([eE][+-]?[0-9]+)?$' THEN 1 ELSE 0 END) "
            f"FROM {rel} "
            f"WHERE {col} IS NOT NULL"
        )
        try:
            row = self._execute(sql).fetchone()
            if not row or row[0] is None or row[0] == 0:
                return 0.0, 0.0
            total, ints, floats = row
            return float(ints or 0) / total, float(floats or 0) / total
        except Exception:
            return 0.0, 0.0

    def probe_numeric_moments(self, table: str, column: str) -> tuple[float, float]:
        from dq_triage.stats.probes import _quote_ident, _quote_relation

        try:
            dtype = self.probe_column_dtype(table, column).upper()
            is_numeric = any(
                t in dtype
                for t in (
                    "INT",
                    "LONG",
                    "SHORT",
                    "TINY",
                    "FLOAT",
                    "DOUBLE",
                    "DECIMAL",
                    "NUMERIC",
                    "REAL",
                )
            )
            if not is_numeric:
                return 0.0, 0.0
            rel = _quote_relation(table)
            col = _quote_ident(column)
            sql = f"SELECT AVG(CAST({col} AS DOUBLE PRECISION)), STDDEV(CAST({col} AS DOUBLE PRECISION)) FROM {rel}"
            row = self._execute(sql).fetchone()
            if not row:
                return 0.0, 0.0
            avg, std = row
            return float(avg or 0.0), float(std or 0.0)
        except Exception:
            return 0.0, 0.0

    def probe_skew(self, table: str, column: str) -> float:
        # Postgres does not have a native SKEW function. We return 0.0 or a basic statistic.
        return 0.0


class SnowflakeAdapter:
    """Mockable stubs for Snowflake warehouse querying."""

    def __init__(self, con: Any) -> None:
        self.con = con

    def probe_row_count(self, table: str) -> int:
        return 0

    def probe_column_dtype(self, table: str, column: str) -> str:
        return "VARCHAR"

    def probe_column_stats(self, table: str, column: str) -> ColumnStats:
        from dq_triage.stats.probes import ColumnStats

        return ColumnStats(table, column, 0, 0, 0, "VARCHAR")

    def probe_dupe_count(
        self, table: str, key_columns: tuple[str, ...], limit: int = 50
    ) -> tuple[DupeKey, ...]:
        return ()

    def probe_freshness(
        self, table: str, timestamp_column: str, observed_at: datetime
    ) -> FreshnessReading:
        from dq_triage.stats.probes import FreshnessReading

        return FreshnessReading(table, timestamp_column, None, observed_at)

    def probe_dtype_distribution(self, table: str, column: str) -> tuple[float, float]:
        return 0.0, 0.0

    def probe_numeric_moments(self, table: str, column: str) -> tuple[float, float]:
        return 0.0, 0.0

    def probe_skew(self, table: str, column: str) -> float:
        return 0.0


class BigQueryAdapter:
    """Mockable stubs for BigQuery warehouse querying."""

    def __init__(self, con: Any) -> None:
        self.con = con

    def probe_row_count(self, table: str) -> int:
        return 0

    def probe_column_dtype(self, table: str, column: str) -> str:
        return "STRING"

    def probe_column_stats(self, table: str, column: str) -> ColumnStats:
        from dq_triage.stats.probes import ColumnStats

        return ColumnStats(table, column, 0, 0, 0, "STRING")

    def probe_dupe_count(
        self, table: str, key_columns: tuple[str, ...], limit: int = 50
    ) -> tuple[DupeKey, ...]:
        return ()

    def probe_freshness(
        self, table: str, timestamp_column: str, observed_at: datetime
    ) -> FreshnessReading:
        from dq_triage.stats.probes import FreshnessReading

        return FreshnessReading(table, timestamp_column, None, observed_at)

    def probe_dtype_distribution(self, table: str, column: str) -> tuple[float, float]:
        return 0.0, 0.0

    def probe_numeric_moments(self, table: str, column: str) -> tuple[float, float]:
        return 0.0, 0.0

    def probe_skew(self, table: str, column: str) -> float:
        return 0.0
