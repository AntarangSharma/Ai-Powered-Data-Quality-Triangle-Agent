"""Unit tests for the self-healing code generator."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dq_triage.agent.self_healing import CodeFixProposal, propose_fix
from dq_triage.attribution.manifest import ManifestNode
from dq_triage.cache import _write_budget_spent, clear_cache
from dq_triage.models import (
    BlameLocation,
    ClassScore,
    Incident,
    RootCauseClass,
    RootCauseHypothesis,
    Verdict,
)


@pytest.fixture(autouse=True)
def clean_cache_and_budget():
    """Reset the disk cache and budget for every test run."""
    clear_cache()
    _write_budget_spent(0.0)


def _create_mock_incident(blame_model: str = "raw_orders") -> Incident:
    hypothesis = RootCauseHypothesis(
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
        blame_model=blame_model,
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
            model=blame_model,
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


@patch("dq_triage.agent.self_healing.Manifest")
def test_self_healing_lookup_error(mock_manifest_cls):
    mock_manifest = MagicMock()
    mock_manifest.by_name = {}
    mock_manifest_cls.return_value = mock_manifest

    incident = _create_mock_incident(blame_model="non_existent")

    with pytest.raises(LookupError, match="not found in dbt manifest"):
        propose_fix(incident, Path("/fake/dir"))


@patch("dq_triage.agent.self_healing.Manifest")
def test_self_healing_value_error_for_non_model(mock_manifest_cls):
    mock_node = ManifestNode(
        unique_id="seed.jaffle_shop.raw_orders",
        name="raw_orders",
        schema="main",
        alias="raw_orders",
        database="warehouse",
        kind="seed",
        original_file_path="seeds/raw_orders.csv",
        compiled_sql_path=None,
    )
    mock_manifest = MagicMock()
    mock_manifest.by_name = {"raw_orders": mock_node}
    mock_manifest_cls.return_value = mock_manifest

    incident = _create_mock_incident(blame_model="raw_orders")

    with pytest.raises(ValueError, match="Cannot heal non-models"):
        propose_fix(incident, Path("/fake/dir"))


@patch("dq_triage.agent.self_healing.Manifest")
@patch("pathlib.Path.exists")
def test_self_healing_file_not_found_error(mock_exists, mock_manifest_cls):
    mock_node = ManifestNode(
        unique_id="model.jaffle_shop.stg_orders",
        name="stg_orders",
        schema="main",
        alias="stg_orders",
        database="warehouse",
        kind="model",
        original_file_path="models/stg_orders.sql",
        compiled_sql_path=Path("/fake/dir/compiled.sql"),
    )
    mock_manifest = MagicMock()
    mock_manifest.by_name = {"stg_orders": mock_node}
    mock_manifest_cls.return_value = mock_manifest
    mock_exists.return_value = False

    incident = _create_mock_incident(blame_model="stg_orders")

    with pytest.raises(FileNotFoundError, match="Source file for model"):
        propose_fix(incident, Path("/fake/dir"))


@patch("dq_triage.agent.self_healing.Manifest")
@patch("pathlib.Path.read_text")
@patch("pathlib.Path.exists")
def test_self_healing_fallback_flow(mock_exists, mock_read_text, mock_manifest_cls):
    mock_node = ManifestNode(
        unique_id="model.jaffle_shop.stg_orders",
        name="stg_orders",
        schema="main",
        alias="stg_orders",
        database="warehouse",
        kind="model",
        original_file_path="models/stg_orders.sql",
        compiled_sql_path=Path("/fake/dir/compiled.sql"),
    )
    mock_manifest = MagicMock()
    mock_manifest.by_name = {"stg_orders": mock_node}
    mock_manifest_cls.return_value = mock_manifest
    mock_exists.return_value = True
    mock_read_text.return_value = "select * from raw_orders;"

    incident = _create_mock_incident(blame_model="stg_orders")

    with patch.dict(os.environ, {}, clear=True):
        if "ANTHROPIC_API_KEY" in os.environ:
            del os.environ["ANTHROPIC_API_KEY"]
        proposal = propose_fix(incident, Path("/fake/dir"))

    assert proposal.file_path == "models/stg_orders.sql"
    assert proposal.original_code == "select * from raw_orders;"
    assert "FIX: Filter out" in proposal.corrected_code
    assert "fallback" in proposal.explanation.lower()
    assert proposal.branch_name == "fix/dq-inc_123"


@patch("dq_triage.agent.self_healing.Manifest")
@patch("pathlib.Path.read_text")
@patch("pathlib.Path.exists")
@patch("instructor.from_anthropic")
def test_self_healing_llm_success(
    mock_from_anthropic, mock_exists, mock_read_text, mock_manifest_cls
):
    mock_node = ManifestNode(
        unique_id="model.jaffle_shop.stg_orders",
        name="stg_orders",
        schema="main",
        alias="stg_orders",
        database="warehouse",
        kind="model",
        original_file_path="models/stg_orders.sql",
        compiled_sql_path=Path("/fake/dir/compiled.sql"),
    )
    mock_manifest = MagicMock()
    mock_manifest.by_name = {"stg_orders": mock_node}
    mock_manifest_cls.return_value = mock_manifest
    mock_exists.return_value = True
    mock_read_text.return_value = "select * from raw_orders;"

    # Mock instructor response
    mock_client = MagicMock()
    mock_from_anthropic.return_value = mock_client

    expected_proposal = CodeFixProposal(
        file_path="models/stg_orders.sql",
        original_code="select * from raw_orders;",
        corrected_code="select coalesce(id, 0) as id from raw_orders;",
        explanation="Added coalesce to handle null spike.",
        branch_name="fix/coalesce-orders",
    )
    mock_completion = MagicMock()
    mock_completion.usage.input_tokens = 10
    mock_completion.usage.output_tokens = 20
    mock_client.messages.create_with_completion.return_value = (expected_proposal, mock_completion)

    incident = _create_mock_incident(blame_model="stg_orders")

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
        proposal = propose_fix(incident, Path("/fake/dir"))

    assert proposal.file_path == "models/stg_orders.sql"
    assert proposal.corrected_code == "select coalesce(id, 0) as id from raw_orders;"
    assert proposal.explanation == "Added coalesce to handle null spike."
    assert proposal.branch_name == "fix/coalesce-orders"
