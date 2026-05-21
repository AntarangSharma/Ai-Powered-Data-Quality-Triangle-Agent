"""SQLAlchemy engine + session factory.

DSN resolution order:

  1. Explicit argument to :func:`get_engine`.
  2. ``DQ_DATABASE_URL`` env var.
  3. Fallback: SQLite at ``<repo>/.local/dq_triage.sqlite``.

Both backends share the same ORM models. Postgres-specific types (``JSONB``,
``ARRAY``) are not used at the model level — we use SQLAlchemy's generic
``JSON`` which compiles to ``JSONB`` on Postgres and ``JSON`` (TEXT) on SQLite.
That keeps the unit tests fast (no Postgres needed) without lying about prod.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATABASE_URL_DEFAULT = f"sqlite:///{_REPO_ROOT / '.local' / 'dq_triage.sqlite'}"


def _resolve_url(url: str | None) -> str:
    if url:
        return url
    return os.environ.get("DQ_DATABASE_URL") or DATABASE_URL_DEFAULT


class Base(DeclarativeBase):
    """Declarative base shared by all ORM models in :mod:`dq_triage.store`."""


@lru_cache(maxsize=4)
def get_engine(url: str | None = None) -> Engine:
    """Return a cached :class:`Engine` for ``url`` (or the default).

    The cache key is the *resolved* URL string, so callers passing ``None``
    twice share an engine. SQLite URLs get ``check_same_thread=False`` so
    test fixtures and the runner can share a connection safely.
    """
    resolved = _resolve_url(url)
    connect_args: dict[str, object] = {}
    if resolved.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # Ensure the parent directory exists for file-backed sqlite.
        if resolved.startswith("sqlite:///") and ":memory:" not in resolved:
            path = Path(resolved.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(resolved, future=True, connect_args=connect_args)


def get_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


# Eagerly-bound module-level helpers for the common case.
engine = get_engine()
SessionLocal = get_sessionmaker()
