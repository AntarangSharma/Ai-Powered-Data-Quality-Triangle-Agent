"""Repository functions over the incident store.

Thin layer between Pydantic models (in :mod:`dq_triage.models`) and the ORM
rows (in :mod:`dq_triage.store.models`). Repository functions do the
serialization in one place so callers never touch SQLAlchemy sessions
directly.

Round-trip invariant
--------------------
``load_incident(save_incident(inc))`` is identical to ``inc`` for every
field defined on :class:`dq_triage.models.Incident`. This is tested in
``tests/unit/test_store_repository.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from dq_triage.models import GroundTruth, Incident, RootCauseClass, Verdict
from dq_triage.store.db import SessionLocal
from dq_triage.store.models import GroundTruthRow, IncidentRow

# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _incident_to_row(inc: Incident) -> IncidentRow:
    final = inc.final_verdict
    return IncidentRow(
        incident_id=inc.incident_id,
        created_at=inc.created_at,
        failing_test_name=inc.failing_test_name,
        failing_model=inc.failing_model,
        failing_column=inc.failing_column,
        failing_row_count=inc.failing_row_count,
        blame_model=inc.blame_location.model,
        blame_column=inc.blame_location.column,
        blame_certainty=inc.blame_location.certainty,
        blame_hit_agg_boundary=inc.blame_location.hit_agg_boundary,
        verdict_type=inc.verdict_type.value,
        final_cause_class=final.cause_class.value if final is not None else None,
        confidence=final.confidence_calibrated if final is not None else None,
        time_to_verdict_s=inc.time_to_verdict_seconds,
        token_cost_usd=inc.token_cost_usd,
        human_label=inc.human_label.value if inc.human_label is not None else None,
        payload=inc.model_dump(mode="json"),
    )


def _row_to_incident(row: IncidentRow) -> Incident:
    return Incident.model_validate(row.payload)


def _ground_truth_to_row(gt: GroundTruth) -> GroundTruthRow:
    return GroundTruthRow(
        incident_key=gt.incident_key,
        cause_class=gt.cause_class.value,
        source_table=gt.source_table,
        source_column=gt.source_column,
        fault_pattern=gt.fault_pattern,
        injected_at=gt.injected_at,
        notes=gt.notes,
        payload=gt.model_dump(mode="json"),
    )


def _row_to_ground_truth(row: GroundTruthRow) -> GroundTruth:
    return GroundTruth.model_validate(row.payload)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_incident(incident: Incident, session: Session | None = None) -> None:
    """UPSERT the incident by primary key. Idempotent."""
    own_session = session is None
    if session is None:
        session = SessionLocal()
    try:
        existing = session.get(IncidentRow, incident.incident_id)
        new_row = _incident_to_row(incident)
        if existing is None:
            session.add(new_row)
        else:
            # In-place update — copy attributes from the freshly serialized row.
            for col in IncidentRow.__table__.columns:
                setattr(existing, col.name, getattr(new_row, col.name))
        if own_session:
            session.commit()
    finally:
        if own_session:
            session.close()


def save_ground_truth(gt: GroundTruth, session: Session | None = None) -> None:
    own_session = session is None
    if session is None:
        session = SessionLocal()
    try:
        existing = session.get(GroundTruthRow, gt.incident_key)
        new_row = _ground_truth_to_row(gt)
        if existing is None:
            session.add(new_row)
        else:
            for col in GroundTruthRow.__table__.columns:
                setattr(existing, col.name, getattr(new_row, col.name))
        if own_session:
            session.commit()
    finally:
        if own_session:
            session.close()


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def load_incident(incident_id: str, session: Session | None = None) -> Incident | None:
    own = session is None
    if session is None:
        session = SessionLocal()
    try:
        row = session.get(IncidentRow, incident_id)
        return _row_to_incident(row) if row is not None else None
    finally:
        if own:
            session.close()


def list_incidents(
    *,
    failing_model: str | None = None,
    cause_class: RootCauseClass | None = None,
    verdict_type: Verdict | None = None,
    limit: int = 100,
    session: Session | None = None,
) -> list[Incident]:
    """Return incidents matching the (optional) filters, newest first."""
    own = session is None
    if session is None:
        session = SessionLocal()
    try:
        stmt = select(IncidentRow).order_by(IncidentRow.created_at.desc()).limit(limit)
        if failing_model is not None:
            stmt = stmt.where(IncidentRow.failing_model == failing_model)
        if cause_class is not None:
            stmt = stmt.where(IncidentRow.final_cause_class == cause_class.value)
        if verdict_type is not None:
            stmt = stmt.where(IncidentRow.verdict_type == verdict_type.value)
        rows: Sequence[IncidentRow] = session.execute(stmt).scalars().all()
        return [_row_to_incident(r) for r in rows]
    finally:
        if own:
            session.close()


def delete_incident(incident_id: str, session: Session | None = None) -> bool:
    own = session is None
    if session is None:
        session = SessionLocal()
    try:
        row = session.get(IncidentRow, incident_id)
        if row is None:
            return False
        session.delete(row)
        if own:
            session.commit()
        return True
    finally:
        if own:
            session.close()


def iter_ground_truths(session: Session | None = None) -> Iterable[GroundTruth]:
    own = session is None
    if session is None:
        session = SessionLocal()
    try:
        for row in session.execute(select(GroundTruthRow)).scalars():
            yield _row_to_ground_truth(row)
    finally:
        if own:
            session.close()


def update_human_label(
    incident_id: str, label: RootCauseClass | None, session: Session | None = None
) -> bool:
    """Update the human feedback label on an existing incident."""
    own = session is None
    if session is None:
        session = SessionLocal()
    try:
        row = session.get(IncidentRow, incident_id)
        if row is None:
            return False
        row.human_label = label.value if label is not None else None
        payload = dict(row.payload)
        payload["human_label"] = label.value if label is not None else None
        row.payload = payload
        if own:
            session.commit()
        return True
    finally:
        if own:
            session.close()
