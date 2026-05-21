"""Persistence layer for incidents and ground truth.

Two backends are supported via SQLAlchemy:

  * **Postgres** (production / CI) — set ``DQ_DATABASE_URL=postgresql+psycopg://…``.
    Uses ``JSONB`` for nested payloads; Alembic owns the schema.
  * **SQLite** (dev / unit tests) — default
    ``sqlite:///<repo>/.local/dq_triage.sqlite``. Same ORM models, ``JSON``
    falls back to TEXT.

The Incident Pydantic model is the source of truth. The ORM models below
expose only the columns we'll routinely query — everything else lives in a
JSON ``payload`` column so we can losslessly round-trip an Incident.
Migration 0002+ can promote frequently-queried JSON paths into typed
columns without losing history.
"""

from dq_triage.store.db import (
    DATABASE_URL_DEFAULT,
    Base,
    SessionLocal,
    engine,
    get_engine,
    get_sessionmaker,
)
from dq_triage.store.models import GroundTruthRow, IncidentRow
from dq_triage.store.repository import (
    delete_incident,
    list_incidents,
    load_incident,
    save_ground_truth,
    save_incident,
)

__all__ = [
    "DATABASE_URL_DEFAULT",
    "Base",
    "GroundTruthRow",
    "IncidentRow",
    "SessionLocal",
    "delete_incident",
    "engine",
    "get_engine",
    "get_sessionmaker",
    "list_incidents",
    "load_incident",
    "save_ground_truth",
    "save_incident",
]
