"""Unit tests for the warehouse adapters."""

from __future__ import annotations

from unittest.mock import MagicMock

import duckdb

from dq_triage.stats.adapter import DuckDBAdapter, PostgresAdapter


def test_duckdb_adapter_delegation():
    # Verify that DuckDBAdapter delegates calls down to probes.py
    con = duckdb.connect()
    # Create simple tables in DuckDB to test actual query execution via the adapter
    con.execute("CREATE TABLE t (id INT, v VARCHAR)")
    con.execute("INSERT INTO t VALUES (1, 'hello'), (2, 'world'), (2, 'world')")

    adapter = DuckDBAdapter(con)

    # Test row count
    assert adapter.probe_row_count("t") == 3

    # Test column dtype
    assert adapter.probe_column_dtype("t", "id") == "INTEGER"
    assert adapter.probe_column_dtype("t", "v") == "VARCHAR"

    # Test stats
    stats = adapter.probe_column_stats("t", "v")
    assert stats.row_count == 3
    assert stats.null_count == 0
    assert stats.distinct_count == 2
    assert stats.dtype == "VARCHAR"

    # Test dupe count
    dupes = adapter.probe_dupe_count("t", ("id",))
    assert len(dupes) == 1
    assert dupes[0].value == "2"
    assert dupes[0].count == 2


def test_postgres_adapter_query_assembly():
    # Verify the SQL strings built by PostgresAdapter
    mock_cursor = MagicMock()

    class MockCon:
        def cursor(self):
            return mock_cursor

    mock_con = MockCon()
    adapter = PostgresAdapter(mock_con)

    # Mock dtype lookup
    mock_cursor.fetchone.side_effect = [
        (42, 0, 42),  # for row_count, null_count, distinct_count
        ("INTEGER",),  # for dtype lookup
    ]

    stats = adapter.probe_column_stats("my_schema.my_table", "my_col")

    assert stats.row_count == 42
    assert stats.null_count == 0
    assert stats.distinct_count == 42
    assert stats.dtype == "INTEGER"

    # Verify executed SQL commands
    calls = mock_cursor.execute.call_args_list
    assert len(calls) == 2

    # First call: stats aggregation
    stats_sql = calls[0][0][0]
    assert "SELECT COUNT(*)" in stats_sql
    assert 'SUM(CASE WHEN "my_col" IS NULL THEN 1 ELSE 0 END)' in stats_sql
    assert 'FROM "my_schema"."my_table"' in stats_sql

    # Second call: information_schema query
    info_sql, info_params = calls[1][0]
    assert "information_schema.columns" in info_sql
    assert info_params == ["my_schema", "my_table", "my_col"]
