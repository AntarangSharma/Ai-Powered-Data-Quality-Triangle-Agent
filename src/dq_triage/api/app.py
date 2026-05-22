"""FastAPI application — one webhook + one health check.

Why this exists separately from the CLI:
  * CI workflows + Airflow on-failure hooks talk HTTP, not subprocess.
  * Letting external services push failures (rather than us pulling
    run_results.json from a shared volume) is the standard pattern.
  * Health checks belong on the network surface, not in argparse.

What it does *not* do:
  * No auth. Deployers gate this behind their existing ingress (NGINX
    basic auth, an IAP-protected Cloud Run service, an internal-only
    NLB, etc.). Adding token auth here would just be security theatre.
  * No queueing. A real load-shedded version would push the request
    onto SQS / Pub-Sub and ack immediately; the agent today runs in
    ~2.5s so synchronous is fine for current scale.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from dq_triage.agent.orchestrator import (
    load_failing_tests,
    triage,
    triage_and_persist,
)
from dq_triage.models import Incident

app = FastAPI(
    title="dq-triage",
    description=(
        "Root-cause attribution for dbt test failures. POST a project + "
        "warehouse path; receive a structured Incident in seconds."
    ),
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class TriageRequest(BaseModel):
    """Inputs to a triage call.

    Mirrors the CLI flags so a webhook caller doesn't have to learn a new
    vocabulary. Paths are server-local — the caller is presumed to share
    a filesystem with the agent (typical k8s deployment with the dbt
    artefacts on a PVC, or both processes in the same CI runner).
    """

    project_dir: str = Field(
        ...,
        description="Server-local path to the dbt project (contains dbt_project.yml).",
    )
    duckdb_path: str = Field(
        ...,
        description="Server-local path to the DuckDB warehouse file.",
    )
    test_name: str | None = Field(
        None,
        description=(
            "Specific failing test to triage. If omitted, the staging-layer "
            "failure closest to the root cause is auto-selected."
        ),
    )
    persist: bool = Field(
        True,
        description="If True, write the resulting Incident to the configured store.",
    )


class HealthResponse(BaseModel):
    status: str
    version: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Liveness probe. Always returns 200; deeper checks (DB, warehouse)
    live behind ``/readyz`` once we have multi-replica deployments."""
    return HealthResponse(status="ok", version=app.version)


@app.post(
    "/webhook/dbt-failure",
    response_model=Incident,
    tags=["triage"],
    responses={
        404: {"description": "No failing tests in run_results.json."},
        422: {"description": "Caller specified a test_name that doesn't match any failure."},
    },
)
def webhook_dbt_failure(req: TriageRequest, notify: str | None = None) -> Incident:
    """Triage a dbt test failure.

    Returns the full ``Incident`` payload (Pydantic, frozen). The same
    object is persisted to the configured store when ``persist=True``,
    keyed by ``incident_id``.
    """
    project = Path(req.project_dir).resolve()
    duckdb_path = Path(req.duckdb_path).resolve()
    if not project.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"project_dir {project} is not a directory",
        )
    if not duckdb_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"duckdb_path {duckdb_path} is not a file",
        )

    failing = load_failing_tests(project)
    if not failing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No failing tests in target/run_results.json - has dbt run?",
        )

    if req.test_name is not None:
        chosen = next(
            (
                f
                for f in failing
                if f.test_name == req.test_name or f.test_name.startswith(req.test_name)
            ),
            None,
        )
        if chosen is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"No failing test matched name {req.test_name!r}. "
                    f"Available: {[f.test_name for f in failing]}"
                ),
            )
    else:
        # Same heuristic as the CLI: staging-layer failures first.
        chosen = sorted(
            failing,
            key=lambda f: (0 if f.model.startswith("stg_") else 1, f.model, f.test_name),
        )[0]

    fn: Any = triage_and_persist if req.persist else triage
    incident: Incident = fn(
        project_dir=project,
        duckdb_path=duckdb_path,
        failing_test=chosen,
    )

    if notify and notify.startswith("slack:"):
        channel = notify.split(":", 1)[1]
        try:
            from dq_triage.narrator import compose, post

            narrated = compose(incident)
            post(narrated, incident, channel)
        except Exception:
            # Prevent notification errors from breaking the main DQ analysis webhook
            pass

    return incident
