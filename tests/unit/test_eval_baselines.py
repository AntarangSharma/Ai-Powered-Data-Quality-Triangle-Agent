"""Unit tests for evaluation baseline models."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from dq_triage.cache import _write_budget_spent, clear_cache
from dq_triage.models import RootCauseClass
from eval.baselines import b1_test_name, b2_elementary, b3_naive_llm, b4_no_llm
from eval.datasets import JAFFLE_SHOP


@pytest.fixture(autouse=True)
def clean_cache_and_budget():
    """Reset the disk cache and budget for every test run."""
    clear_cache()
    _write_budget_spent(0.0)


@pytest.fixture
def db_con():
    c = duckdb.connect(":memory:")
    c.execute("CREATE SCHEMA main_staging")
    c.execute("CREATE TABLE raw_orders (id INTEGER, user_id INTEGER, status VARCHAR)")
    c.execute("CREATE TABLE raw_customers (id INTEGER, name VARCHAR)")
    c.execute("INSERT INTO raw_customers VALUES (1,'a'),(2,'b'),(3,'c'),(4,'d')")
    c.execute(
        "INSERT INTO raw_orders VALUES "
        "(1, 1, 'open'),(2, 2, 'open'),(3, 3, 'shipped'),(4, 4, 'returned')"
    )
    yield c
    c.close()


class MockTrial:
    def __init__(self, pattern_id: str = "pattern_123", seed: int = 1):
        self.fault = MagicMock()
        self.fault.pattern_id = pattern_id
        self.seed = seed


class MockFailingTest:
    def __init__(
        self,
        test_name: str = "not_null_stg_orders_user_id",
        model: str = "stg_orders",
        column: str = "user_id",
        kind: str = "not_null",
        failures_table_fqn: str = "main_staging.not_null_stg_orders_user_id",
    ):
        self.test_name = test_name
        self.model = model
        self.column = column
        self.kind = kind
        self.failures_table_fqn = failures_table_fqn


def test_b1_test_name():
    trial = MockTrial()
    chosen = MockFailingTest(kind="not_null")
    pred = b1_test_name.predict(con=None, cfg=JAFFLE_SHOP, chosen=chosen, trial=trial)

    assert pred.incident_key == "pattern_123_1"
    assert pred.candidate_tables == ("stg_orders",)
    assert pred.blame_column == "user_id"
    assert pred.cause_class == RootCauseClass.UPSTREAM_NULL_SPIKE
    assert pred.confidence == 1.0


def test_b2_elementary():
    trial = MockTrial()
    chosen = MockFailingTest()
    pred = b2_elementary.predict(con=None, cfg=JAFFLE_SHOP, chosen=chosen, trial=trial)

    assert pred.incident_key == "pattern_123_1"
    assert len(pred.candidate_tables) == 1
    assert pred.candidate_tables[0] in ("raw_customers", "raw_orders", "raw_payments")
    assert pred.blame_column == "user_id"
    assert pred.cause_class in (
        RootCauseClass.UPSTREAM_NULL_SPIKE,
        RootCauseClass.DUPLICATE_INGESTION,
        RootCauseClass.BROKEN_JOIN_DROPOUT,
    )
    assert pred.confidence == 0.33


@patch("instructor.from_anthropic")
def test_b3_naive_llm_success(mock_from_anthropic, db_con):
    mock_client = MagicMock()
    mock_from_anthropic.return_value = mock_client

    expected_response = b3_naive_llm.LLMOnlyResponse(
        blame_table="raw_orders",
        blame_column="user_id",
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
        reason="Detected high null values on source ingestion",
    )
    mock_client.messages.create.return_value = expected_response

    trial = MockTrial()
    chosen = MockFailingTest()

    # Create failures table in the memory DB so B3 can select from it
    db_con.execute("CREATE TABLE main_staging.not_null_stg_orders_user_id (id INTEGER)")
    db_con.execute("INSERT INTO main_staging.not_null_stg_orders_user_id VALUES (1)")

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        pred = b3_naive_llm.predict(con=db_con, cfg=JAFFLE_SHOP, chosen=chosen, trial=trial)

    assert pred.incident_key == "pattern_123_1"
    assert pred.candidate_tables == ("raw_orders",)
    assert pred.blame_column == "user_id"
    assert pred.cause_class == RootCauseClass.UPSTREAM_NULL_SPIKE
    assert pred.confidence == 0.9


def test_b3_naive_llm_fallback(db_con):
    trial = MockTrial()
    chosen = MockFailingTest(test_name="unique_stg_customers_customer_id", kind="unique")

    with patch.dict(os.environ, {}, clear=True):
        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        pred = b3_naive_llm.predict(con=db_con, cfg=JAFFLE_SHOP, chosen=chosen, trial=trial)

    assert pred.incident_key == "pattern_123_1"
    assert pred.candidate_tables == ("raw_customers",)
    assert pred.cause_class == RootCauseClass.DUPLICATE_INGESTION
    assert pred.confidence == 0.8


@patch("dq_triage.attribution.sqlglot_walker.SqlglotWalker.attribute")
@patch("instructor.from_anthropic")
def test_b4_no_llm_success(mock_from_anthropic, mock_attribute, db_con):
    mock_client = MagicMock()
    mock_from_anthropic.return_value = mock_client

    expected_response = b4_no_llm.B4Response(
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
        confidence=0.92,
        reason="Identified upstream null spike based on probes",
    )
    mock_client.messages.create.return_value = expected_response

    # Mock SQLGlotWalker attribute
    mock_blame = MagicMock()
    mock_blame.model = "raw_orders"
    mock_blame.column = "user_id"
    mock_blame.row_pks = [1, 2, 3]
    mock_attribute.return_value = mock_blame

    trial = MockTrial()
    chosen = MockFailingTest()

    db_con.execute("CREATE TABLE main_staging.not_null_stg_orders_user_id (id INTEGER)")

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        pred = b4_no_llm.predict(con=db_con, cfg=JAFFLE_SHOP, chosen=chosen, trial=trial)

    assert pred.incident_key == "pattern_123_1"
    assert pred.candidate_tables == ("raw_orders",)
    assert pred.blame_column == "user_id"
    assert pred.cause_class == RootCauseClass.UPSTREAM_NULL_SPIKE
    assert pred.confidence == 0.92


@patch("dq_triage.attribution.sqlglot_walker.SqlglotWalker.attribute")
def test_b4_no_llm_fallback(mock_attribute, db_con):
    mock_blame = MagicMock()
    mock_blame.model = "raw_orders"
    mock_blame.column = "user_id"
    mock_blame.row_pks = [1, 2, 3]
    mock_attribute.return_value = mock_blame

    trial = MockTrial()
    chosen = MockFailingTest(kind="not_null")

    db_con.execute("CREATE TABLE main_staging.not_null_stg_orders_user_id (id INTEGER)")

    with patch.dict(os.environ, {}, clear=True):
        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        pred = b4_no_llm.predict(con=db_con, cfg=JAFFLE_SHOP, chosen=chosen, trial=trial)

    assert pred.incident_key == "pattern_123_1"
    assert pred.candidate_tables == ("raw_orders",)
    assert pred.cause_class == RootCauseClass.UPSTREAM_NULL_SPIKE
    assert pred.confidence == 0.8
