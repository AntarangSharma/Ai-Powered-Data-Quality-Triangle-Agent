"""Alembic environment.

We resolve the DSN at runtime so the same migrations apply against
production Postgres and dev SQLite. Set ``DQ_DATABASE_URL`` to point at
something other than the default SQLite file.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from dq_triage.store.db import DATABASE_URL_DEFAULT
from dq_triage.store.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Tell Alembic which metadata to autogenerate against.
target_metadata = Base.metadata


def _resolved_url() -> str:
    import os

    return os.environ.get("DQ_DATABASE_URL") or DATABASE_URL_DEFAULT


def run_migrations_offline() -> None:
    context.configure(
        url=_resolved_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _resolved_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            render_as_batch=connection.dialect.name == "sqlite",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
