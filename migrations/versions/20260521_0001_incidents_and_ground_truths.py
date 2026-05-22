"""Migration 0001: incidents and ground_truths tables.

Revision ID: 0001
Revises:
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(length=64), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failing_test_name", sa.Text(), nullable=False),
        sa.Column("failing_model", sa.String(length=128), nullable=False),
        sa.Column("failing_column", sa.String(length=128), nullable=True),
        sa.Column("failing_row_count", sa.Integer(), nullable=False),
        sa.Column("blame_model", sa.String(length=128), nullable=False),
        sa.Column("blame_column", sa.String(length=128), nullable=True),
        sa.Column("blame_certainty", sa.Float(), nullable=False),
        sa.Column(
            "blame_hit_agg_boundary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("verdict_type", sa.String(length=32), nullable=False),
        sa.Column("final_cause_class", sa.String(length=64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("time_to_verdict_s", sa.Float(), nullable=False),
        sa.Column(
            "token_cost_usd",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("human_label", sa.String(length=64), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"], unique=False)
    op.create_index("ix_incidents_failing_model", "incidents", ["failing_model"], unique=False)
    op.create_index(
        "ix_incidents_final_cause_class",
        "incidents",
        ["final_cause_class"],
        unique=False,
    )

    op.create_table(
        "ground_truths",
        sa.Column("incident_key", sa.String(length=64), primary_key=True),
        sa.Column("cause_class", sa.String(length=64), nullable=False),
        sa.Column("source_table", sa.String(length=128), nullable=False),
        sa.Column("source_column", sa.String(length=128), nullable=True),
        sa.Column("fault_pattern", sa.String(length=128), nullable=False),
        sa.Column("injected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("payload", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_ground_truths_cause_class",
        "ground_truths",
        ["cause_class"],
        unique=False,
    )
    op.create_index(
        "ix_ground_truths_fault_pattern",
        "ground_truths",
        ["fault_pattern"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_ground_truths_fault_pattern", table_name="ground_truths")
    op.drop_index("ix_ground_truths_cause_class", table_name="ground_truths")
    op.drop_table("ground_truths")
    op.drop_index("ix_incidents_final_cause_class", table_name="incidents")
    op.drop_index("ix_incidents_failing_model", table_name="incidents")
    op.drop_index("ix_incidents_created_at", table_name="incidents")
    op.drop_table("incidents")
