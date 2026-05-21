"""SQLAlchemy ORM models for the incident store.

Schema design (migration 0001):

``incidents``
    incident_id            TEXT  PK
    created_at             TIMESTAMP with tz
    failing_test_name      TEXT
    failing_model          TEXT
    failing_column         TEXT  nullable
    failing_row_count      INT
    blame_model            TEXT
    blame_column           TEXT  nullable
    blame_certainty        FLOAT
    blame_hit_agg_boundary BOOL
    verdict_type           TEXT
    final_cause_class      TEXT  nullable  (None ⇔ TRIAGE_ONLY)
    confidence             FLOAT nullable
    time_to_verdict_s      FLOAT
    token_cost_usd         FLOAT
    human_label            TEXT  nullable  (filled by Slack 👍/👎)
    payload                JSON           full Incident JSON
    indices: (created_at), (failing_model), (final_cause_class)

``ground_truths``
    incident_key           TEXT  PK
    cause_class            TEXT
    source_table           TEXT
    source_column          TEXT  nullable
    fault_pattern          TEXT
    injected_at            TIMESTAMP with tz
    notes                  TEXT
    payload                JSON           full GroundTruth JSON
    indices: (cause_class), (fault_pattern)

Why JSON payload alongside typed columns: lets us evolve the Incident shape
without a migration every week, while still letting BI tools and the eval
runner slice by the common dimensions without parsing JSON.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from dq_triage.store.db import Base


class IncidentRow(Base):
    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    failing_test_name: Mapped[str] = mapped_column(Text, nullable=False)
    failing_model: Mapped[str] = mapped_column(String(128), nullable=False)
    failing_column: Mapped[str | None] = mapped_column(String(128))
    failing_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    blame_model: Mapped[str] = mapped_column(String(128), nullable=False)
    blame_column: Mapped[str | None] = mapped_column(String(128))
    blame_certainty: Mapped[float] = mapped_column(Float, nullable=False)
    blame_hit_agg_boundary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verdict_type: Mapped[str] = mapped_column(String(32), nullable=False)
    final_cause_class: Mapped[str | None] = mapped_column(String(64))
    confidence: Mapped[float | None] = mapped_column(Float)
    time_to_verdict_s: Mapped[float] = mapped_column(Float, nullable=False)
    token_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    human_label: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_incidents_created_at", "created_at"),
        Index("ix_incidents_failing_model", "failing_model"),
        Index("ix_incidents_final_cause_class", "final_cause_class"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<IncidentRow id={self.incident_id!r} "
            f"model={self.failing_model!r} class={self.final_cause_class!r}>"
        )


class GroundTruthRow(Base):
    __tablename__ = "ground_truths"

    incident_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    cause_class: Mapped[str] = mapped_column(String(64), nullable=False)
    source_table: Mapped[str] = mapped_column(String(128), nullable=False)
    source_column: Mapped[str | None] = mapped_column(String(128))
    fault_pattern: Mapped[str] = mapped_column(String(128), nullable=False)
    injected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_ground_truths_cause_class", "cause_class"),
        Index("ix_ground_truths_fault_pattern", "fault_pattern"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<GroundTruthRow key={self.incident_key!r} "
            f"class={self.cause_class!r} pattern={self.fault_pattern!r}>"
        )
