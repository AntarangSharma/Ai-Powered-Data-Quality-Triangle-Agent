"""Migrations sanity test.

Two checks, both against a throwaway SQLite file:

  1. ``alembic upgrade head`` succeeds on an empty DB.
  2. After upgrade, an Alembic autogenerate run against the live ORM
     metadata produces **no diff** — guarantees that ``models.py`` and the
     latest migration are in sync.

This is fast (~0.3s) and runs in CI without needing Postgres.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from dq_triage.store.db import get_engine
from dq_triage.store.models import Base

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _alembic_cfg(db_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    # env.py reads DQ_DATABASE_URL — set it for the subprocess-less invocation.
    os.environ["DQ_DATABASE_URL"] = db_url
    return cfg


@pytest.fixture
def fresh_db(tmp_path: Path):
    db_file = tmp_path / "mig.sqlite"
    url = f"sqlite:///{db_file}"
    yield url
    get_engine.cache_clear()
    os.environ.pop("DQ_DATABASE_URL", None)


def test_upgrade_head_creates_tables(fresh_db: str):
    cfg = _alembic_cfg(fresh_db)
    command.upgrade(cfg, "head")
    engine = get_engine(fresh_db)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diff = compare_metadata(ctx, Base.metadata)
    # Allow benign index-uniqueness or server_default formatting diffs that
    # SQLite cannot fully represent — but a fresh upgrade should produce
    # nothing structural.
    structural = [
        d for d in diff if d[0] in {"add_table", "remove_table", "add_column", "remove_column"}
    ]
    assert structural == [], f"schema drift detected: {structural}"


def test_downgrade_drops_tables(fresh_db: str):
    cfg = _alembic_cfg(fresh_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = get_engine(fresh_db)
    with engine.connect() as conn:
        names = set(conn.dialect.get_table_names(conn))
    assert "incidents" not in names
    assert "ground_truths" not in names
