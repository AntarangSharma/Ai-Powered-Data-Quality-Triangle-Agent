"""Statistical probes — small parameterized SQL queries that snapshot the
state of a (table, column) pair for the classifier.

Why these and not Great Expectations / dbt-expectations:
  - We need the *raw numbers* (null_count, row_count, distinct_count, …) as
    primitives the classifier rules combine. GE returns pass/fail; we need
    floats.
  - Every probe is one parameterized SQL statement against a DuckDB cursor.
    No reflection, no ORM, no warmup.
  - All identifiers are validated (regex-checked) to keep SQL injection
    impossible — even though the inputs come from our own manifest, we
    treat them as untrusted as a discipline.

A probe never raises on missing data; it returns a snapshot with whatever
shape the warehouse can give us. The classifier is responsible for deciding
what "null_count == row_count" means (table is empty / column doesn't exist).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

# ---------------------------------------------------------------------------
# Identifier safety. DuckDB allows unicode identifiers but our pipelines all
# use snake_case ASCII — and the only callers are inside our own manifest.
# Keep the regex strict so a typo can't smuggle SQL through.
# ---------------------------------------------------------------------------

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_ident(name: str) -> str:
    """Validate and quote a SQL identifier (table or column name)."""
    if not _IDENT_RE.match(name):
        raise ValueError(f"Invalid SQL identifier: {name!r}")
    # DuckDB uses double quotes for identifier quoting.
    return f'"{name}"'


def _quote_relation(relation: str) -> str:
    """Validate and quote a possibly-schema-qualified relation: ``schema.table``
    or just ``table``."""
    parts = relation.split(".")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"Invalid relation: {relation!r}")
    return ".".join(_quote_ident(p) for p in parts)


# ---------------------------------------------------------------------------
# Result types — frozen dataclasses (not Pydantic) because probes are hot-path
# enough that we want zero-validation construction. We DO validate at the
# Incident-record boundary via `UpstreamStat` in `dq_triage.models`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColumnStats:
    """Per-column snapshot."""

    table: str
    column: str
    row_count: int
    null_count: int
    distinct_count: int
    dtype: str

    @property
    def null_rate(self) -> float:
        return self.null_count / self.row_count if self.row_count else 0.0

    @property
    def cardinality_ratio(self) -> float:
        """distinct_count / row_count — close to 1.0 for PK-like columns,
        close to 0 for low-cardinality categoricals. Useful for the classifier
        to distinguish 'broken FK' (cardinality drops) from 'null spike'
        (cardinality stable, nulls rise)."""
        non_null = self.row_count - self.null_count
        return self.distinct_count / non_null if non_null else 0.0


@dataclass(frozen=True, slots=True)
class DupeKey:
    """One duplicate value of a key, with its observed count."""

    value: str
    count: int


@dataclass(frozen=True, slots=True)
class FreshnessReading:
    """Most recent timestamp in a column, and how stale that is *now*."""

    table: str
    timestamp_column: str
    max_timestamp: datetime | None
    observed_at: datetime

    @property
    def lag_seconds(self) -> float | None:
        if self.max_timestamp is None:
            return None
        return (self.observed_at - self.max_timestamp).total_seconds()


# ---------------------------------------------------------------------------
# Probes
# ---------------------------------------------------------------------------


def probe_row_count(con: Any, table: str) -> int:
    """Total row count of `table` (validated identifier)."""
    if hasattr(con, "probe_row_count"):
        return con.probe_row_count(table)
    sql = f"SELECT COUNT(*) FROM {_quote_relation(table)}"
    (n,) = con.execute(sql).fetchone()  # type: ignore[misc]
    return int(n)


def probe_column_dtype(con: Any, table: str, column: str) -> str:
    """DuckDB-reported SQL type for `table.column`.

    Uses `information_schema.columns`. Schema-qualified tables are split.
    Returns the raw DuckDB type string (e.g. "VARCHAR", "INTEGER").
    """
    if hasattr(con, "probe_column_dtype"):
        return con.probe_column_dtype(table, column)
    parts = table.split(".")
    if len(parts) == 1:
        schema_clause = ""
        params = [parts[0], column]
    elif len(parts) == 2:
        schema_clause = "table_schema = ? AND "
        params = [parts[0], parts[1], column]
    else:
        # database.schema.table — DuckDB's information_schema is per-database
        # so we drop the database part here. We still validate it.
        _quote_ident(parts[0])
        schema_clause = "table_schema = ? AND "
        params = [parts[1], parts[2], column]
    # Validate the rest.
    for p in (params[-2], params[-1]):
        _quote_ident(p)
    sql = (
        "SELECT data_type FROM information_schema.columns "
        f"WHERE {schema_clause}table_name = ? AND column_name = ?"
    )
    row = con.execute(sql, params).fetchone()
    if row is None:
        raise LookupError(f"Column not found: {table}.{column}")
    return str(row[0])


def probe_column_stats(con: Any, table: str, column: str) -> ColumnStats:
    """Compute row_count, null_count, distinct_count, dtype for `table.column`
    in one SQL round-trip (plus one for dtype via info schema)."""
    if hasattr(con, "probe_column_stats"):
        return con.probe_column_stats(table, column)
    rel = _quote_relation(table)
    col = _quote_ident(column)
    sql = (
        f"SELECT COUNT(*), "
        f"       SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END), "
        f"       COUNT(DISTINCT {col}) "
        f"FROM {rel}"
    )
    row = con.execute(sql).fetchone()
    assert row is not None  # COUNT always returns a row
    row_count, null_count, distinct_count = row
    dtype = probe_column_dtype(con, table, column)
    return ColumnStats(
        table=table,
        column=column,
        row_count=int(row_count),
        null_count=int(null_count or 0),
        distinct_count=int(distinct_count),
        dtype=dtype,
    )


def probe_dupe_count(
    con: Any,
    table: str,
    key_columns: tuple[str, ...],
    limit: int = 50,
) -> tuple[DupeKey, ...]:
    """Find values of `key_columns` that appear more than once.

    Returns up to `limit` `DupeKey` rows ordered by count descending. Empty
    tuple means the key is unique (or the table is empty)."""
    if hasattr(con, "probe_dupe_count"):
        return con.probe_dupe_count(table, key_columns, limit)
    if not key_columns:
        raise ValueError("key_columns must be non-empty")
    cols_sql = ", ".join(_quote_ident(c) for c in key_columns)
    rel = _quote_relation(table)
    # Use STRING_AGG-friendly cast for the value display so non-string keys
    # don't blow up the DupeKey.value (str) contract.
    key_concat = " || '|' || ".join(f"CAST({_quote_ident(c)} AS VARCHAR)" for c in key_columns)
    sql = (
        f"SELECT ({key_concat}) AS k, COUNT(*) AS c "
        f"FROM {rel} "
        f"GROUP BY {cols_sql} "
        f"HAVING COUNT(*) > 1 "
        f"ORDER BY c DESC "
        f"LIMIT {int(limit)}"
    )
    rows = con.execute(sql).fetchall()
    return tuple(DupeKey(value=str(v), count=int(c)) for v, c in rows)


def probe_freshness(
    con: Any,
    table: str,
    timestamp_column: str,
    observed_at: datetime,
) -> FreshnessReading:
    """Max timestamp in `table.timestamp_column` (== how recent the data is)."""
    if hasattr(con, "probe_freshness"):
        return con.probe_freshness(table, timestamp_column, observed_at)
    rel = _quote_relation(table)
    col = _quote_ident(timestamp_column)
    sql = f"SELECT MAX({col}) FROM {rel}"
    row = con.execute(sql).fetchone()
    max_ts = None if row is None or row[0] is None else _coerce_datetime(row[0])
    return FreshnessReading(
        table=table,
        timestamp_column=timestamp_column,
        max_timestamp=max_ts,
        observed_at=observed_at,
    )


def _coerce_datetime(v: object) -> datetime:
    """DuckDB may return `date` or `datetime` — normalise to datetime."""
    if isinstance(v, datetime):
        return v
    # `date` quacks like datetime via fromisoformat / datetime constructor.
    from datetime import date

    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    return datetime.fromisoformat(str(v))


def probe_dtype_distribution(con: Any, table: str, column: str) -> tuple[float, float]:
    """Compute what percentage of non-null values are integer or float compatible in VARCHAR.

    Returns (integer_compatible_ratio, float_compatible_ratio).
    """
    if hasattr(con, "probe_dtype_distribution"):
        return con.probe_dtype_distribution(table, column)
    dtype = probe_column_dtype(con, table, column).upper()
    if "VARCHAR" not in dtype and "TEXT" not in dtype and "STRING" not in dtype:
        return 0.0, 0.0
    rel = _quote_relation(table)
    col = _quote_ident(column)
    sql = (
        f"SELECT COUNT(*), "
        f"       SUM(CASE WHEN TRY_CAST({col} AS BIGINT) IS NOT NULL THEN 1 ELSE 0 END), "
        f"       SUM(CASE WHEN TRY_CAST({col} AS DOUBLE) IS NOT NULL THEN 1 ELSE 0 END) "
        f"FROM {rel} "
        f"WHERE {col} IS NOT NULL"
    )
    try:
        row = con.execute(sql).fetchone()
        if row is None or row[0] is None or row[0] == 0:
            return 0.0, 0.0
        total, ints, floats = row
        return float(ints or 0) / total, float(floats or 0) / total
    except Exception:
        return 0.0, 0.0


def probe_numeric_moments(con: Any, table: str, column: str) -> tuple[float, float]:
    """Compute the mean and standard deviation for a numeric column."""
    if hasattr(con, "probe_numeric_moments"):
        return con.probe_numeric_moments(table, column)
    try:
        dtype = probe_column_dtype(con, table, column).upper()
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
        sql = f"SELECT AVG(CAST({col} AS DOUBLE)), STDDEV(CAST({col} AS DOUBLE)) FROM {rel}"
        row = con.execute(sql).fetchone()
        if row is None:
            return 0.0, 0.0
        avg, std = row
        return float(avg or 0.0), float(std or 0.0)
    except Exception:
        return 0.0, 0.0


def probe_skew(con: Any, table: str, column: str) -> float:
    """Compute the skewness of a numeric column."""
    if hasattr(con, "probe_skew"):
        return con.probe_skew(table, column)
    try:
        dtype = probe_column_dtype(con, table, column).upper()
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
            return 0.0
        rel = _quote_relation(table)
        col = _quote_ident(column)
        sql = f"SELECT SKEW(CAST({col} AS DOUBLE)) FROM {rel}"
        row = con.execute(sql).fetchone()
        if row is None or row[0] is None:
            return 0.0
        return float(row[0])
    except Exception:
        return 0.0
