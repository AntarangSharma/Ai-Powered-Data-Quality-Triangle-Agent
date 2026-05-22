"""FastAPI app tests.

We exercise the HTTP surface (routing, validation, error codes) without
touching the warehouse. The orchestrator has its own tests; here we only
assert that the API contract holds.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dq_triage.api import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _write_dbt_artifacts(target_dir: Path, results: list[dict], nodes: dict) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "run_results.json").write_text(json.dumps({"results": results}))
    (target_dir / "manifest.json").write_text(json.dumps({"nodes": nodes}))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


def test_health_returns_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"]  # truthy


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


def test_webhook_rejects_missing_fields(client):
    r = client.post("/webhook/dbt-failure", json={})
    assert r.status_code == 422  # FastAPI validation error
    body = r.json()
    assert "detail" in body


def test_webhook_400_when_project_dir_not_a_dir(client, tmp_path):
    not_a_dir = tmp_path / "nope"
    duckdb_file = tmp_path / "wh.duckdb"
    duckdb_file.touch()
    r = client.post(
        "/webhook/dbt-failure",
        json={
            "project_dir": str(not_a_dir),
            "duckdb_path": str(duckdb_file),
            "persist": False,
        },
    )
    assert r.status_code == 400
    assert "not a directory" in r.json()["detail"]


def test_webhook_400_when_duckdb_path_not_a_file(client, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    r = client.post(
        "/webhook/dbt-failure",
        json={
            "project_dir": str(project),
            "duckdb_path": str(tmp_path / "nope.duckdb"),
            "persist": False,
        },
    )
    assert r.status_code == 400
    assert "not a file" in r.json()["detail"]


def test_webhook_404_when_no_failing_tests(client, tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    duckdb_file = tmp_path / "wh.duckdb"
    duckdb_file.touch()
    r = client.post(
        "/webhook/dbt-failure",
        json={
            "project_dir": str(project),
            "duckdb_path": str(duckdb_file),
            "persist": False,
        },
    )
    assert r.status_code == 404
    assert "No failing tests" in r.json()["detail"]


def test_webhook_422_when_test_name_doesnt_match(client, tmp_path):
    project = tmp_path / "proj"
    target = project / "target"
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
    r = client.post(
        "/webhook/dbt-failure",
        json={
            "project_dir": str(project),
            "duckdb_path": str(duckdb_file),
            "test_name": "no_such_test",
            "persist": False,
        },
    )
    assert r.status_code == 422
    assert "No failing test matched" in r.json()["detail"]


# ---------------------------------------------------------------------------
# OpenAPI schema sanity
# ---------------------------------------------------------------------------


def test_openapi_lists_both_endpoints(client):
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"].keys())
    assert paths == {"/health", "/webhook/dbt-failure"}


def test_openapi_incident_schema_present(client):
    """If this breaks, the response_model wiring is broken — callers
    won't be able to generate typed SDKs."""
    schema = client.get("/openapi.json").json()
    components = schema.get("components", {}).get("schemas", {})
    assert "Incident" in components
    assert "TriageRequest" in components


# ---------------------------------------------------------------------------
# Integration: live triage against the Jaffle warehouse
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_webhook_end_to_end_on_jaffle(client):
    """Round-trip the same call the CLI makes, through the API surface."""
    repo_root = Path(__file__).resolve().parents[2]
    project = repo_root / "pipelines" / "jaffle_shop"
    duckdb_path = project / "warehouse.duckdb"
    if not (project / "target" / "run_results.json").exists() or not duckdb_path.exists():
        pytest.skip("Jaffle warehouse not built; run `make eval-smoke` first.")
    r = client.post(
        "/webhook/dbt-failure",
        json={
            "project_dir": str(project),
            "duckdb_path": str(duckdb_path),
            "persist": False,
        },
    )
    if r.status_code == 404:
        pytest.skip("No failing tests in current run_results — nothing to triage.")
    assert r.status_code == 200
    body = r.json()
    assert body["incident_id"].startswith("inc_")
    assert "verdict_type" in body
    assert "class_scores" in body and len(body["class_scores"]) >= 1
