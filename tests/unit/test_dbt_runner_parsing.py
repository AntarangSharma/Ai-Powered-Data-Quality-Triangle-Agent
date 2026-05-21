"""Test the dbt_runner test-name parser without spawning dbt."""

from __future__ import annotations

from eval.dbt_runner import _parse_test_name


def test_not_null_parses() -> None:
    assert _parse_test_name("not_null_stg_orders_customer_id", "stg_orders") == (
        "stg_orders",
        "customer_id",
    )


def test_unique_parses() -> None:
    assert _parse_test_name("unique_stg_customers_customer_id", "stg_customers") == (
        "stg_customers",
        "customer_id",
    )


def test_relationships_returns_best_effort_column() -> None:
    # dbt mangles relationships test names; we accept best-effort column.
    model, col = _parse_test_name(
        "relationships_stg_orders_customer_id__customer_id__ref_stg_customers_",
        "stg_orders",
    )
    assert model == "stg_orders"
    assert col == "customer_id"


def test_unknown_test_type() -> None:
    model, col = _parse_test_name("some_custom_test_stg_orders_x", "stg_orders")
    assert model == "stg_orders"
    assert col is None


def test_no_depends_on() -> None:
    assert _parse_test_name("some_singular_test", None) == ("?", None)
