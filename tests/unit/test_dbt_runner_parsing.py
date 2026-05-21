"""Test the dbt_runner test-name parser without spawning dbt."""

from __future__ import annotations

from eval.dbt_runner import _parse_test_name


def test_not_null_parses() -> None:
    assert _parse_test_name(
        "not_null_stg_orders_customer_id", ["stg_orders"]
    ) == ("stg_orders", "customer_id")


def test_unique_parses() -> None:
    assert _parse_test_name(
        "unique_stg_customers_customer_id", ["stg_customers"]
    ) == ("stg_customers", "customer_id")


def test_relationships_picks_source_model() -> None:
    """For `relationships`, depends_on has BOTH the source and target models.
    The parser must pick the source (the one whose column owns the FK)."""
    model, col = _parse_test_name(
        "relationships_stg_orders_customer_id__customer_id__ref_stg_customers_",
        # Order chosen to verify we don't just pick the first.
        ["stg_customers", "stg_orders"],
    )
    assert model == "stg_orders"
    assert col == "customer_id"


def test_relationships_prefers_longer_model_name() -> None:
    """If two model names both prefix-match, the longer one wins (avoids
    a false hit when one model name is a prefix of another)."""
    # Synthetic: model 'stg' AND 'stg_orders' both exist in some project.
    model, col = _parse_test_name(
        "not_null_stg_orders_customer_id", ["stg", "stg_orders"]
    )
    assert model == "stg_orders"


def test_unknown_test_type_falls_back_to_first_model() -> None:
    model, col = _parse_test_name(
        "some_custom_test_stg_orders_x", ["stg_orders"]
    )
    assert model == "stg_orders"
    assert col is None


def test_no_depends_on() -> None:
    assert _parse_test_name("some_singular_test", []) == ("?", None)


def test_accepted_values_parses() -> None:
    """accepted_values test names get suffix-hashed by dbt — we recover the
    model + column from the prefix."""
    model, col = _parse_test_name(
        "accepted_values_stg_orders_status__placed__shipped__completed",
        ["stg_orders"],
    )
    assert model == "stg_orders"
    assert col == "status"
