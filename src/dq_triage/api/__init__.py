"""FastAPI handler exposing the triage pipeline over HTTP.

The same pipeline that powers the CLI (``dq-triage triage``) is reachable
here at ``POST /webhook/dbt-failure`` — so a CI workflow, an Airflow
on-failure callback, or an external incident manager can fire-and-forget
a triage request and get a structured ``Incident`` back.

Run locally::

    uvicorn dq_triage.api:app --reload --port 8080

The app is intentionally tiny — just the orchestrator behind one
endpoint plus a health check. All real logic stays in
``dq_triage.agent.orchestrator``.
"""

from dq_triage.api.app import app

__all__ = ["app"]
