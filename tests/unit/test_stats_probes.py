"""Unit tests for `dq_triage.stats.probes`."""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pytest

from dq_triage.stats.probes import (
    ColumnStats,
    DupeKey,
    FreshnessReading,
    _quote_ident,
    _quote_relation,
    probe_column_dtype,
    probe_column_stats,
    probe_dupe_count,
    probe_freshness,
    probe_row_count,
)

# ---------------------------------------------------------------------------
# Identifier safety
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["users", "_x", "abc123", "snake_case_99"])
def test_quote_ident_accepts_valid(name: str) -> None:
    assert _quote_ident(name) == f'"{name}"'


@pytest.mark.parametrize(
    "name",
    [
        "",
        "1abc",  # leading digit
        "drop table",  # space
        'users";--',  # injection attempt
        "weird-name",  # hyphen
        "user.name",  # dot — relation_quote handles, ident must reject
    ],
)
def test_quote_ident_rejects_invalid(name: str) -> None:
    with pytest.raises(ValueError):
        _quote_ident(name)


def test_quote_relation_handles_schema() -> None:
    assert _quote_relation("main.users") == '"main"."users"'


def test_quote_relation_rejects_too_many_parts() -> None:
    with pytest.raises(ValueError):
        _quote_relation("a.b.c.d")


# ---------------------------------------------------------------------------
# Probes (against an in-memory DuckDB)
# ---------------------------------------------------------------------------


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    c.execute("""
        CREATE TABLE users (
            id INTEGER,
            email VARCHAR,
            created_at TIMESTAMP
        )
    """)
    c.execute("""
        INSERT INTO users VALUES
            (1, 'a@x.com', '2026-01-01 00:00:00'),
            (2, 'b@x.com', '2026-01-02 00:00:00'),
            (3, NULL,      '2026-01-03 00:00:00'),
            (4, 'a@x.com', '2026-01-04 00:00:00')  -- dupe email
    """)
    return c


def test_probe_row_count(con: duckdb.DuckDBPyConnection) -> None:
    assert probe_row_count(con, "users") == 4


def test_probe_row_count_rejects_bad_identifier(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError):
        probe_row_count(con, "users; DROP TABLE users")


def test_probe_column_dtype(con: duckdb.DuckDBPyConnection) -> None:
    assert probe_column_dtype(con, "users", "id") == "INTEGER"
    assert probe_column_dtype(con, "users", "email") == "VARCHAR"


def test_probe_column_dtype_missing_column(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(LookupError):
        probe_column_dtype(con, "users", "does_not_exist")


def test_probe_column_stats_basic(con: duckdb.DuckDBPyConnection) -> None:
    s = probe_column_stats(con, "users", "email")
    assert s.row_count == 4
    assert s.null_count == 1
    assert s.distinct_count == 2  # 'a@x.com', 'b@x.com'  (NULL excluded by COUNT DISTINCT)
    assert s.null_rate == pytest.approx(0.25)
    # cardinality_ratio = 2 distinct / 3 non-null = 0.666…
    assert s.cardinality_ratio == pytest.approx(2 / 3)
    assert s.dtype == "VARCHAR"


def test_probe_column_stats_empty_table() -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE t (x INTEGER)")
    s = probe_column_stats(c, "t", "x")
    assert s.row_count == 0
    assert s.null_count == 0
    assert s.distinct_count == 0
    assert s.null_rate == 0.0
    assert s.cardinality_ratio == 0.0


def test_probe_column_stats_all_nulls() -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE t (x INTEGER)")
    c.execute("INSERT INTO t VALUES (NULL), (NULL), (NULL)")
    s = probe_column_stats(c, "t", "x")
    assert s.row_count == 3
    assert s.null_count == 3
    assert s.null_rate == 1.0
    assert s.cardinality_ratio == 0.0  # no non-null rows


def test_probe_dupe_count_finds_dupes(con: duckdb.DuckDBPyConnection) -> None:
    dupes = probe_dupe_count(con, "users", ("email",))
    assert dupes == (DupeKey(value="a@x.com", count=2),)


def test_probe_dupe_count_returns_empty_when_unique(con: duckdb.DuckDBPyConnection) -> None:
    # id is unique in our fixture
    assert probe_dupe_count(con, "users", ("id",)) == ()


def test_probe_dupe_count_composite_key() -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE orders (user_id INT, day DATE)")
    c.execute("INSERT INTO orders VALUES (1, '2026-01-01'), (1, '2026-01-01'), (2, '2026-01-02')")
    dupes = probe_dupe_count(c, "orders", ("user_id", "day"))
    assert len(dupes) == 1
    assert dupes[0].count == 2


def test_probe_dupe_count_rejects_empty_keys(con: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError):
        probe_dupe_count(con, "users", ())


def test_probe_freshness_returns_max(con: duckdb.DuckDBPyConnection) -> None:
    now = datetime(2026, 1, 5, tzinfo=None)
    fr = probe_freshness(con, "users", "created_at", observed_at=now)
    assert isinstance(fr, FreshnessReading)
    assert fr.max_timestamp == datetime(2026, 1, 4)
    assert fr.lag_seconds == pytest.approx(86400)  # one day


def test_probe_freshness_empty_table() -> None:
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE t (ts TIMESTAMP)")
    fr = probe_freshness(c, "t", "ts", observed_at=datetime.now(UTC))
    assert fr.max_timestamp is None
    assert fr.lag_seconds is None


# ---------------------------------------------------------------------------
# Property-ish: ColumnStats invariants
# ---------------------------------------------------------------------------


def test_column_stats_null_rate_bounded() -> None:
    s = ColumnStats("t", "c", row_count=100, null_count=37, distinct_count=20, dtype="INT")
    assert 0.0 <= s.null_rate <= 1.0
    assert s.null_rate == 0.37


def test_column_stats_cardinality_ratio_bounded() -> None:
    s = ColumnStats("t", "c", row_count=100, null_count=10, distinct_count=90, dtype="INT")
    assert s.cardinality_ratio == pytest.approx(1.0)
