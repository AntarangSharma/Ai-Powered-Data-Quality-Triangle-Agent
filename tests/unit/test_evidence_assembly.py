"""Integration tests for :func:`assemble_evidence` against in-memory DuckDB."""

from __future__ import annotations

import duckdb
import pytest

from dq_triage.agent.evidence import assemble_evidence


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE TABLE raw_orders (id INTEGER, user_id INTEGER, status VARCHAR)")
    c.execute("CREATE TABLE raw_customers (id INTEGER, name VARCHAR)")
    c.execute("INSERT INTO raw_customers VALUES (1,'a'),(2,'b'),(3,'c'),(4,'d')")
    c.execute(
        "INSERT INTO raw_orders VALUES "
        "(1, 1, 'open'),(2, 2, 'open'),(3, 3, 'shipped'),(4, 4, 'returned')"
    )
    yield c
    c.close()


def test_clean_warehouse_has_no_signal(con):
    e = assemble_evidence(
        con,
        failing_test_kind="not_null",
        failing_model="stg_orders",
        failing_column="customer_id",
        blame_model="raw_orders",
        blame_column="user_id",
        blame_pk_column="id",
    )
    assert e.blame_row_count == 4
    assert e.blame_null_rate == 0.0
    assert e.blame_pk_dupe_count == 0
    assert e.orphan_fk_count == 0


def test_null_rate_is_probed(con):
    con.execute("UPDATE raw_orders SET user_id = NULL WHERE id = 1")
    e = assemble_evidence(
        con,
        failing_test_kind="not_null",
        failing_model="stg_orders",
        failing_column="customer_id",
        blame_model="raw_orders",
        blame_column="user_id",
        blame_pk_column="id",
    )
    assert e.blame_null_rate == 0.25  # 1/4


def test_pk_dupe_count_is_extra_rows(con):
    # Insert two more rows with id=2 → 3 copies of PK=2 → 2 extras.
    con.execute("INSERT INTO raw_orders VALUES (2, 99, 'x'),(2, 100, 'y')")
    e = assemble_evidence(
        con,
        failing_test_kind="unique",
        failing_model="stg_orders",
        failing_column="order_id",
        blame_model="raw_orders",
        blame_column="id",
        blame_pk_column="id",
    )
    assert e.blame_pk_dupe_count == 2


def test_orphan_fk_counted_for_relationships(con):
    # Delete customer 1 → row 1 in raw_orders is orphaned.
    con.execute("DELETE FROM raw_customers WHERE id = 1")
    e = assemble_evidence(
        con,
        failing_test_kind="relationships",
        failing_model="stg_orders",
        failing_column="customer_id",
        blame_model="raw_orders",
        blame_column="user_id",
        blame_pk_column="id",
        parent_table="raw_customers",
        parent_pk_column="id",
    )
    assert e.orphan_fk_count == 1
    assert e.parent_row_count == 3


def test_orphan_fk_zero_when_kind_not_relationships(con):
    """Parent probe shouldn't fire for not_null/unique tests."""
    e = assemble_evidence(
        con,
        failing_test_kind="not_null",
        failing_model="stg_orders",
        failing_column="customer_id",
        blame_model="raw_orders",
        blame_column="user_id",
        blame_pk_column="id",
        parent_table="raw_customers",
        parent_pk_column="id",
    )
    assert e.orphan_fk_count == 0
    assert e.parent_row_count is None


def test_assembly_is_defensive_on_missing_column(con):
    e = assemble_evidence(
        con,
        failing_test_kind="not_null",
        failing_model="raw_orders",
        failing_column="does_not_exist",
        blame_model="raw_orders",
        blame_column="does_not_exist",
        blame_pk_column="id",
    )
    # Should not raise; defaults out.
    assert e.blame_null_rate == 0.0
    assert e.blame_row_count == 4  # row count still works
