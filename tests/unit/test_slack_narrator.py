"""Unit tests for the Slack narrator components (composer.py and slack.py)."""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from dq_triage.cache import (
    BudgetExceededError,
    _write_budget_spent,
    clear_cache,
    read_budget_spent,
)
from dq_triage.models import (
    BlameLocation,
    ClassScore,
    Incident,
    RootCauseClass,
    RootCauseHypothesis,
    Verdict,
)
from dq_triage.narrator.composer import NarratedIncident, compose
from dq_triage.narrator.slack import post
from dq_triage.store.repository import update_human_label


def _create_mock_incident() -> Incident:
    hypothesis = RootCauseHypothesis(
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
        blame_model="raw_orders",
        blame_column="user_id",
        blame_rows_sample=(),
        confidence=0.9,
        confidence_calibrated=0.88,
        evidence_summary="Null spike detected on upstream orders table.",
        suggested_one_line_fix="Check raw_orders ingestion pipeline.",
    )
    return Incident(
        incident_id="inc_123",
        created_at=datetime.utcnow(),
        failing_test_name="not_null_stg_orders_customer_id",
        failing_model="stg_orders",
        failing_column="customer_id",
        failing_row_count=100,
        failing_rows_sample=(),
        blame_location=BlameLocation(
            model="raw_orders",
            column="user_id",
            certainty=1.0,
        ),
        upstream_stats=(),
        class_scores=(ClassScore(cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE, score=0.9),),
        hypotheses=(hypothesis,),
        verdict_type=Verdict.AUTO,
        final_verdict=hypothesis,
        time_to_verdict_seconds=1.5,
        token_cost_usd=0.002,
    )


@pytest.fixture(autouse=True)
def clean_cache_and_budget():
    """Reset the disk cache and budget for every test run."""
    clear_cache()
    _write_budget_spent(0.0)


# ---------------------------------------------------------------------------
# Composer Tests
# ---------------------------------------------------------------------------


def test_composer_fallback_when_no_api_key():
    incident = _create_mock_incident()
    with patch.dict(os.environ, {}, clear=True):
        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        result = compose(incident)

    assert result.headline == "🚨 Data Quality Failure: not_null_stg_orders_customer_id"
    assert "fallback mode" in result.narrative
    assert result.one_line_fix == "Check raw_orders ingestion pipeline."


@patch("instructor.from_anthropic")
def test_composer_mock_success(mock_from_anthropic):
    mock_client = MagicMock()
    mock_from_anthropic.return_value = mock_client

    mock_response = NarratedIncident(
        headline="🚨 Upstream Null Spike on raw_orders.user_id",
        narrative="1) Critical null spike on raw_orders.user_id.\n\n2) Attributed to raw_orders.\n\n3) Needs immediate ingestion pipe review.",
        one_line_fix="SELECT * FROM raw_orders WHERE user_id IS NULL LIMIT 10;",
    )
    mock_completion = MagicMock()
    mock_completion.usage.input_tokens = 500
    mock_completion.usage.output_tokens = 200

    mock_client.messages.create_with_completion.return_value = (mock_response, mock_completion)

    incident = _create_mock_incident()
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        result = compose(incident)

    assert result.headline == "🚨 Upstream Null Spike on raw_orders.user_id"
    assert "Critical null spike" in result.narrative
    assert result.one_line_fix == "SELECT * FROM raw_orders WHERE user_id IS NULL LIMIT 10;"

    # Input: 500 * (3.00/1e6) = 0.0015
    # Output: 200 * (15.00/1e6) = 0.003
    # Total cost = 0.0045
    assert read_budget_spent() == pytest.approx(0.0045)


@patch("instructor.from_anthropic")
def test_composer_cache_hit(mock_from_anthropic):
    mock_client = MagicMock()
    mock_from_anthropic.return_value = mock_client

    mock_response = NarratedIncident(
        headline="Headline",
        narrative="Narrative",
        one_line_fix="Fix",
    )
    mock_completion = MagicMock()
    mock_completion.usage.input_tokens = 10
    mock_completion.usage.output_tokens = 10
    mock_client.messages.create_with_completion.return_value = (mock_response, mock_completion)

    incident = _create_mock_incident()
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        res1 = compose(incident)
        res2 = compose(incident)

    assert res1 == res2
    assert mock_client.messages.create_with_completion.call_count == 1


def test_composer_budget_exceeded():
    incident = _create_mock_incident()
    _write_budget_spent(0.60)  # default max is $0.50

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "DQ_LLM_BUDGET_USD": "0.50"}):
        with pytest.raises(BudgetExceededError, match="LLM budget limit of \\$0.5000 exceeded"):
            compose(incident)


# ---------------------------------------------------------------------------
# Slack Poster Tests
# ---------------------------------------------------------------------------


def test_slack_post_fallback_no_token():
    narrated = NarratedIncident(
        headline="Test Headline",
        narrative="Test Narrative",
        one_line_fix="Fix Command",
    )
    incident = _create_mock_incident()

    with patch.dict(os.environ, {}, clear=True):
        if "SLACK_BOT_TOKEN" in os.environ:
            del os.environ["SLACK_BOT_TOKEN"]
        ts = post(narrated, incident, "dq-alerts")

    assert ts == "1234567890.123456"


@patch("dq_triage.narrator.slack.WebClient")
def test_slack_post_success(mock_web_client_cls):
    mock_client = MagicMock()
    mock_web_client_cls.return_value = mock_client
    mock_client.chat_postMessage.return_value = {"ts": "1716336000.123456"}

    narrated = NarratedIncident(
        headline="Test Headline",
        narrative="Test Narrative",
        one_line_fix="Fix Command",
    )
    incident = _create_mock_incident()

    with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-test-token"}):
        ts = post(narrated, incident, "dq-alerts")

    mock_web_client_cls.assert_called_once_with(token="xoxb-test-token")
    mock_client.chat_postMessage.assert_called_once()
    args, kwargs = mock_client.chat_postMessage.call_args
    assert kwargs["channel"] == "dq-alerts"
    assert "blocks" in kwargs
    assert ts == "1716336000.123456"


# ---------------------------------------------------------------------------
# Repository human feedback update
# ---------------------------------------------------------------------------


def test_update_human_label_not_found():
    result = update_human_label("no_such_incident", RootCauseClass.UPSTREAM_NULL_SPIKE)
    assert result is False
