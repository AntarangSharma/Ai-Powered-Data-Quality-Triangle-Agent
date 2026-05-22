"""Unit tests for the GitHub narrator component."""

from __future__ import annotations

import json
import os
from datetime import datetime
from unittest.mock import MagicMock, patch

from dq_triage.models import (
    BlameLocation,
    ClassScore,
    Incident,
    RootCauseClass,
    RootCauseHypothesis,
    Verdict,
)
from dq_triage.narrator.github_narrator import post_pr_comment


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
    # Using timezone-aware datetime or standard naive
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


def test_github_narrator_prints_payload_if_env_missing():
    incident = _create_mock_incident()
    with patch.dict(os.environ, {}, clear=True):
        res = post_pr_comment(incident)
    assert res is False


@patch("urllib.request.urlopen")
def test_github_narrator_post_success(mock_urlopen):
    incident = _create_mock_incident()

    # Mock response and its context manager __enter__
    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = b'{"id": 999}'
    mock_urlopen.return_value = mock_resp

    env = {
        "GITHUB_TOKEN": "mock-token",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_PR_NUMBER": "42",
    }

    with patch.dict(os.environ, env, clear=True):
        res = post_pr_comment(incident)

    assert res is True
    # Verify urlopen was called with the Request object
    assert mock_urlopen.call_count == 1
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://api.github.com/repos/owner/repo/issues/42/comments"
    assert req.headers["Authorization"] == "Bearer mock-token"
    assert req.headers["Accept"] == "application/vnd.github+json"

    # Verify request body contains the correct text
    body = json.loads(req.data.decode("utf-8"))["body"]
    assert "not_null_stg_orders_customer_id" in body
    assert "inc_123" in body
    assert "raw_orders" in body


@patch("urllib.request.urlopen")
def test_github_narrator_parse_event_path(mock_urlopen, tmp_path):
    incident = _create_mock_incident()
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"number": 101}))

    mock_resp = MagicMock()
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.read.return_value = b'{"id": 1001}'
    mock_urlopen.return_value = mock_resp

    env = {
        "GITHUB_TOKEN": "mock-token",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_EVENT_PATH": str(event_file),
    }

    with patch.dict(os.environ, env, clear=True):
        res = post_pr_comment(incident)

    assert res is True
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://api.github.com/repos/owner/repo/issues/101/comments"
