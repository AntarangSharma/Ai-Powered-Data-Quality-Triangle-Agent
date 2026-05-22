"""Unit tests for the user-facing orchestrator + Typer CLI.

We don't re-test the pipeline internals here (they have their own unit
tests). The goal is to lock down the *contracts*:

  * ``load_failing_tests`` parses dbt's run_results.json / manifest.json.
  * ``triage`` returns a fully-populated, frozen ``Incident`` whose verdict
    classification matches what the rules classifier would emit standalone.
  * The Typer CLI exits 2 when there's nothing to triage and 0 on success.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dq_triage.agent.orchestrator import (
    FailingTest,
    _parse_test_name,
    _verdict_for,
    load_failing_tests,
)
from dq_triage.cli import app
from dq_triage.models import ClassScore, RootCauseClass, Verdict

# ---------------------------------------------------------------------------
# _parse_test_name
# ---------------------------------------------------------------------------


def test_parse_not_null():
    model, col = _parse_test_name("not_null_stg_orders_customer_id", ["stg_orders"], "not_null")
    assert (model, col) == ("stg_orders", "customer_id")


def test_parse_relationships_strips_hash():
    model, col = _parse_test_name(
        "relationships_stg_orders_customer_id__customer_id__ref_stg_customers_",
        ["stg_orders", "stg_customers"],
        "relationships",
    )
    assert (model, col) == ("stg_orders", "customer_id")


def test_parse_longest_model_wins():
    """When multiple models could prefix-match, pick the longest one."""
    model, col = _parse_test_name("unique_stg_orders_order_id", ["stg", "stg_orders"], "unique")
    assert (model, col) == ("stg_orders", "order_id")


def test_parse_unknown_kind_returns_first_dep():
    """Defensive: unparseable kind should not raise."""
    model, col = _parse_test_name("some_custom_test", ["my_model"], "unknown")
    assert (model, col) == ("my_model", None)


# ---------------------------------------------------------------------------
# _verdict_for
# ---------------------------------------------------------------------------


def _cs(cls: RootCauseClass, score: float) -> ClassScore:
    return ClassScore(cause_class=cls, score=score)


def test_verdict_auto_when_confident_and_clear():
    scores = (
        _cs(RootCauseClass.DUPLICATE_INGESTION, 0.95),
        _cs(RootCauseClass.UPSTREAM_NULL_SPIKE, 0.20),
    )
    assert _verdict_for(scores) is Verdict.AUTO


def test_verdict_two_candidate_when_close_call():
    """top-1 ≥ 0.6 but gap < 0.15 → surface both."""
    scores = (
        _cs(RootCauseClass.DUPLICATE_INGESTION, 0.90),
        _cs(RootCauseClass.UPSTREAM_NULL_SPIKE, 0.85),
    )
    assert _verdict_for(scores) is Verdict.TWO_CANDIDATE


def test_verdict_triage_only_when_weak():
    scores = (_cs(RootCauseClass.UNKNOWN, 0.4),)
    assert _verdict_for(scores) is Verdict.TRIAGE_ONLY


def test_verdict_empty_is_triage_only():
    assert _verdict_for(()) is Verdict.TRIAGE_ONLY


# ---------------------------------------------------------------------------
# load_failing_tests
# ---------------------------------------------------------------------------


def _write_dbt_artifacts(target_dir: Path, results: list[dict], nodes: dict) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "run_results.json").write_text(json.dumps({"results": results}))
    (target_dir / "manifest.json").write_text(json.dumps({"nodes": nodes}))


def test_load_failing_tests_picks_failures_only(tmp_path):
    target = tmp_path / "target"
    nodes = {
        "test.proj.not_null_stg_orders_user_id": {
            "name": "not_null_stg_orders_user_id",
            "depends_on": {"nodes": ["model.proj.stg_orders"]},
            "relation_name": '"main_dbt_test_failures"."not_null_stg_orders_user_id"',
        },
        "test.proj.unique_stg_orders_order_id": {
            "name": "unique_stg_orders_order_id",
            "depends_on": {"nodes": ["model.proj.stg_orders"]},
            "relation_name": '"main_dbt_test_failures"."unique_stg_orders_order_id"',
        },
    }
    _write_dbt_artifacts(
        target,
        results=[
            {
                "unique_id": "test.proj.not_null_stg_orders_user_id",
                "status": "fail",
                "relation_name": '"main_dbt_test_failures"."not_null_stg_orders_user_id"',
            },
            {"unique_id": "test.proj.unique_stg_orders_order_id", "status": "pass"},
            # Non-test rows must be ignored.
            {"unique_id": "model.proj.stg_orders", "status": "success"},
        ],
        nodes=nodes,
    )
    out = load_failing_tests(tmp_path)
    assert [t.test_name for t in out] == ["not_null_stg_orders_user_id"]
    t = out[0]
    assert t.model == "stg_orders"
    assert t.column == "user_id"
    assert t.kind == "not_null"
    assert t.failures_table_fqn == "main_dbt_test_failures.not_null_stg_orders_user_id"
    assert t.parent_model is None


def test_load_failing_tests_extracts_relationships_parent(tmp_path):
    target = tmp_path / "target"
    nodes = {
        "test.proj.rel_test": {
            "name": "relationships_stg_orders_customer_id__customer_id__ref_stg_customers_",
            "depends_on": {
                "nodes": [
                    "model.proj.stg_orders",
                    "model.proj.stg_customers",
                ]
            },
            "test_metadata": {"kwargs": {"field": "customer_id", "to": "ref('stg_customers')"}},
            "relation_name": '"main_dbt_test_failures"."rel_test"',
        },
    }
    _write_dbt_artifacts(
        target,
        results=[
            {
                "unique_id": "test.proj.rel_test",
                "status": "fail",
                "relation_name": '"main_dbt_test_failures"."rel_test"',
            },
        ],
        nodes=nodes,
    )
    out = load_failing_tests(tmp_path)
    assert len(out) == 1
    t = out[0]
    assert t.kind == "relationships"
    assert t.model == "stg_orders"
    assert t.parent_model == "stg_customers"
    assert t.parent_column == "customer_id"


def test_load_failing_tests_empty_when_no_artifacts(tmp_path):
    """No run_results.json yet → empty list, no crash."""
    assert load_failing_tests(tmp_path) == []


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_version(runner):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "dq-triage v" in result.stdout


def test_cli_triage_exit_2_when_no_failing_tests(runner, tmp_path):
    """No target/ → CLI must exit 2, not crash."""
    project = tmp_path / "proj"
    project.mkdir()
    duckdb_file = tmp_path / "wh.duckdb"
    duckdb_file.touch()
    result = runner.invoke(
        app,
        [
            "triage",
            "--project",
            str(project),
            "--duckdb",
            str(duckdb_file),
            "--no-persist",
        ],
    )
    assert result.exit_code == 2
    assert "No failing tests" in result.stdout


def test_cli_triage_rejects_unknown_test_name(runner, tmp_path):
    target = tmp_path / "target"
    _write_dbt_artifacts(
        target,
        results=[
            {
                "unique_id": "test.proj.not_null_stg_orders_user_id",
                "status": "fail",
                "relation_name": '"main_dbt_test_failures"."not_null_stg_orders_user_id"',
            },
        ],
        nodes={
            "test.proj.not_null_stg_orders_user_id": {
                "name": "not_null_stg_orders_user_id",
                "depends_on": {"nodes": ["model.proj.stg_orders"]},
                "relation_name": '"main_dbt_test_failures"."not_null_stg_orders_user_id"',
            },
        },
    )
    duckdb_file = tmp_path / "wh.duckdb"
    duckdb_file.touch()
    result = runner.invoke(
        app,
        [
            "triage",
            "--project",
            str(tmp_path),
            "--duckdb",
            str(duckdb_file),
            "--test",
            "does_not_exist",
            "--no-persist",
        ],
    )
    assert result.exit_code == 1
    assert "No failing test matched" in result.stdout


# ---------------------------------------------------------------------------
# Integration: full pipeline against the Jaffle warehouse (smoke)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_triage_end_to_end_on_jaffle(tmp_path, monkeypatch):
    """If the Jaffle warehouse + dbt target/ exist, run the full pipeline.

    This is a smoke test, not a contract test — exact verdict depends on
    which fault was last applied. We just assert the Incident is
    structurally valid and persisted round-trips losslessly.
    """
    repo_root = Path(__file__).resolve().parents[2]
    project = repo_root / "pipelines" / "jaffle_shop"
    duckdb_path = project / "warehouse.duckdb"
    if not (project / "target" / "run_results.json").exists() or not duckdb_path.exists():
        pytest.skip("Jaffle warehouse not built; run `make eval-smoke` first.")

    failing = load_failing_tests(project)
    if not failing:
        pytest.skip("No failing tests in current run_results — nothing to triage.")

    from dq_triage.agent.orchestrator import triage

    incident = triage(
        project_dir=project,
        duckdb_path=duckdb_path,
        failing_test=failing[0],
    )
    assert incident.incident_id.startswith("inc_")
    assert incident.failing_test_name == failing[0].test_name
    assert len(incident.class_scores) >= 1
    assert incident.verdict_type in {Verdict.AUTO, Verdict.TWO_CANDIDATE, Verdict.TRIAGE_ONLY}
    # Round-trip: model_dump_json → re-parse must produce an equal Incident.
    from dq_triage.models import Incident as _I

    assert _I.model_validate_json(incident.model_dump_json()) == incident


def test_failing_test_dataclass_is_frozen():
    """Defensive — protects against accidental mutation in handlers."""
    t = FailingTest(
        test_name="x",
        model="m",
        column="c",
        failures_table_fqn="t",
        kind="not_null",
    )
    # Frozen dataclasses raise FrozenInstanceError on attribute assignment.
    with pytest.raises(AttributeError):
        t.test_name = "y"  # type: ignore[misc]
