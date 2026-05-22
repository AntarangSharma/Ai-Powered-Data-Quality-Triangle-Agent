"""Unit tests for the Anthropic Haiku LLM tiebreaker."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from dq_triage.agent.evidence import ClassifierEvidence
from dq_triage.classification.haiku_tiebreaker import (
    BudgetExceededError,
    HaikuTiebreakerResponse,
    TiebreakerRanking,
    _write_budget_spent,
    haiku_tiebreaker,
    read_budget_spent,
)
from dq_triage.models import ClassScore, RootCauseClass


def _ev() -> ClassifierEvidence:
    return ClassifierEvidence(
        failing_test_kind="not_null",
        failing_model="stg_orders",
        failing_column="customer_id",
        blame_model="raw_orders",
        blame_column="user_id",
        blame_pk_column="id",
        blame_row_count=1000,
    )


@pytest.fixture(autouse=True)
def clean_cache_and_budget():
    """Reset the disk cache and budget for every test run."""
    from dq_triage.cache import clear_cache

    clear_cache()
    _write_budget_spent(0.0)


@patch("instructor.from_anthropic")
def test_haiku_tiebreaker_mock_success(mock_from_anthropic):
    # Set up mocked response
    mock_client = MagicMock()
    mock_from_anthropic.return_value = mock_client

    mock_response = HaikuTiebreakerResponse(
        ranked_classes=[
            TiebreakerRanking(
                cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
                score=0.95,
                reason="High null rate on customer_id",
            ),
            TiebreakerRanking(
                cause_class=RootCauseClass.DUPLICATE_INGESTION,
                score=0.05,
                reason="No duplicate PKs found",
            ),
        ]
    )
    mock_completion = MagicMock()
    mock_completion.usage.input_tokens = 200
    mock_completion.usage.output_tokens = 100

    mock_client.messages.create_with_completion.return_value = (mock_response, mock_completion)

    # Prepare input
    ev = _ev()
    ranked = (ClassScore(cause_class=RootCauseClass.UNKNOWN, score=0.5),)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        result = haiku_tiebreaker(ev, ranked)

    assert len(result) == 2
    assert result[0].cause_class == RootCauseClass.UPSTREAM_NULL_SPIKE
    assert result[0].score == 0.95
    assert result[0].evidence_keys == ("llm_tiebreaker",)
    assert result[1].cause_class == RootCauseClass.DUPLICATE_INGESTION
    assert result[1].score == 0.05

    # Check budget was incremented
    # 200 * (0.25 / 1e6) + 100 * (1.25 / 1e6) = 0.00005 + 0.000125 = 0.000175
    assert read_budget_spent() == pytest.approx(0.000175)


@patch("instructor.from_anthropic")
def test_haiku_tiebreaker_cache_hit(mock_from_anthropic):
    mock_client = MagicMock()
    mock_from_anthropic.return_value = mock_client

    mock_response = HaikuTiebreakerResponse(
        ranked_classes=[
            TiebreakerRanking(
                cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
                score=0.95,
                reason="High null rate on customer_id",
            )
        ]
    )
    mock_completion = MagicMock()
    mock_completion.usage.input_tokens = 10
    mock_completion.usage.output_tokens = 10
    mock_client.messages.create_with_completion.return_value = (mock_response, mock_completion)

    ev = _ev()
    ranked = (ClassScore(cause_class=RootCauseClass.UNKNOWN, score=0.5),)

    # First call: cache miss, calls API
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        result1 = haiku_tiebreaker(ev, ranked)

    # Second call: cache hit, does not call API
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        result2 = haiku_tiebreaker(ev, ranked)

    assert result1 == result2
    assert mock_client.messages.create_with_completion.call_count == 1


def test_haiku_tiebreaker_budget_exceeded():
    ev = _ev()
    ranked = (ClassScore(cause_class=RootCauseClass.UNKNOWN, score=0.5),)

    # Set spent to exceed the default $0.50 budget
    _write_budget_spent(0.60)

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key", "DQ_LLM_BUDGET_USD": "0.50"}):
        with pytest.raises(BudgetExceededError, match="budget limit of \\$0.5000 exceeded"):
            haiku_tiebreaker(ev, ranked)
