"""Unit tests for the incident store repository.

Uses a file-backed SQLite database per test (clean schema each time) so we
don't depend on Postgres being available in CI. Round-trip tests assert that
``load_incident(save_incident(inc))`` is bit-for-bit identical to ``inc`` —
that's the invariant we depend on everywhere downstream.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from dq_triage.models import (
    BlameLocation,
    ClassScore,
    GroundTruth,
    Incident,
    LineageEdge,
    RootCauseClass,
    RootCauseHypothesis,
    UpstreamStat,
    Verdict,
)
from dq_triage.store import (
    Base,
    GroundTruthRow,
    IncidentRow,
    delete_incident,
    get_engine,
    get_sessionmaker,
    list_incidents,
    load_incident,
    save_ground_truth,
    save_incident,
)
from dq_triage.store.repository import iter_ground_truths

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_url(tmp_path: Path) -> str:
    """A fresh file-backed SQLite per test. We avoid :memory: so the engine
    cache key is distinct per test (lru_cache keys on the URL string)."""
    db_file = tmp_path / "store.sqlite"
    return f"sqlite:///{db_file}"


@pytest.fixture
def session_factory(sqlite_url: str):
    engine = get_engine(sqlite_url)
    Base.metadata.create_all(engine)
    yield get_sessionmaker(sqlite_url)
    Base.metadata.drop_all(engine)
    get_engine.cache_clear()


def _sample_blame() -> BlameLocation:
    return BlameLocation(
        model="raw_orders",
        column="user_id",
        row_pks=("1", "2", "3"),
        certainty=1.0,
        walk_path=(
            LineageEdge(
                downstream_model="stg_orders",
                downstream_column="customer_id",
                upstream_model="raw_orders",
                upstream_column="user_id",
                transform_type="DIRECT",
                attribution_certainty=1.0,
                source="sqlglot",
            ),
        ),
        hit_agg_boundary=False,
    )


def _sample_hypothesis() -> RootCauseHypothesis:
    return RootCauseHypothesis(
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
        blame_model="raw_orders",
        blame_column="user_id",
        blame_rows_sample=({"id": 1, "user_id": None},),
        confidence=0.92,
        confidence_calibrated=0.88,
        evidence_summary="user_id null_rate jumped from 0.0% to 5.0%",
        suggested_one_line_fix="Investigate the upstream loader for raw_orders.",
    )


def _sample_incident(incident_id: str = "inc-0001") -> Incident:
    return Incident(
        incident_id=incident_id,
        created_at=datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
        failing_test_name="not_null_stg_orders_customer_id",
        failing_model="stg_orders",
        failing_column="customer_id",
        failing_row_count=24,
        failing_rows_sample=({"order_id": 1, "customer_id": None},),
        blame_location=_sample_blame(),
        upstream_stats=(
            UpstreamStat(
                model="raw_orders",
                column="user_id",
                null_rate_today=0.05,
                null_rate_p50_30d=0.0,
                null_rate_z=12.3,
                distinct_count=99,
                dtype="INTEGER",
                last_loaded_at=datetime(2026, 5, 21, 11, 30, tzinfo=UTC),
                rows_in_last_load=100,
                anomaly_score=0.95,
            ),
        ),
        class_scores=(
            ClassScore(
                cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
                score=0.92,
                evidence_keys=("null_rate_z",),
            ),
        ),
        hypotheses=(_sample_hypothesis(),),
        verdict_type=Verdict.AUTO,
        final_verdict=_sample_hypothesis(),
        human_label=None,
        time_to_verdict_seconds=3.21,
        token_cost_usd=0.0042,
    )


def _sample_ground_truth(key: str = "gt-0001") -> GroundTruth:
    return GroundTruth(
        incident_key=key,
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
        source_table="raw_orders",
        source_column="user_id",
        offending_row_pks=("1", "2", "3"),
        injected_at=datetime(2026, 5, 21, 12, 0, tzinfo=UTC),
        fault_pattern="null_spike.flat_5pct",
        notes="seed=1",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_incident_roundtrip_is_lossless(session_factory):
    session = session_factory()
    try:
        inc = _sample_incident()
        save_incident(inc, session=session)
        session.commit()
        loaded = load_incident(inc.incident_id, session=session)
        assert loaded is not None
        assert loaded == inc
    finally:
        session.close()


def test_save_incident_is_idempotent(session_factory):
    session = session_factory()
    try:
        inc = _sample_incident()
        save_incident(inc, session=session)
        save_incident(inc, session=session)
        session.commit()
        count = session.query(IncidentRow).count()
        assert count == 1
    finally:
        session.close()


def test_update_incident_replaces_payload(session_factory):
    session = session_factory()
    try:
        inc = _sample_incident()
        save_incident(inc, session=session)
        session.commit()
        # Mutate one field by constructing a new Incident with same id.
        inc2 = inc.model_copy(update={"failing_row_count": 999})
        save_incident(inc2, session=session)
        session.commit()
        loaded = load_incident(inc.incident_id, session=session)
        assert loaded is not None
        assert loaded.failing_row_count == 999
        # Only one row total.
        assert session.query(IncidentRow).count() == 1
    finally:
        session.close()


def test_typed_columns_match_payload(session_factory):
    session = session_factory()
    try:
        inc = _sample_incident()
        save_incident(inc, session=session)
        session.commit()
        row = session.get(IncidentRow, inc.incident_id)
        assert row is not None
        # Spot-check projected columns.
        assert row.failing_model == inc.failing_model
        assert row.blame_model == inc.blame_location.model
        assert row.blame_certainty == inc.blame_location.certainty
        assert row.blame_hit_agg_boundary is False
        assert row.final_cause_class == RootCauseClass.UPSTREAM_NULL_SPIKE.value
        assert row.verdict_type == Verdict.AUTO.value
        assert row.confidence == inc.final_verdict.confidence_calibrated
    finally:
        session.close()


def test_triage_only_incident_has_null_verdict_columns(session_factory):
    session = session_factory()
    try:
        inc = _sample_incident().model_copy(
            update={"verdict_type": Verdict.TRIAGE_ONLY, "final_verdict": None}
        )
        save_incident(inc, session=session)
        session.commit()
        row = session.get(IncidentRow, inc.incident_id)
        assert row is not None
        assert row.final_cause_class is None
        assert row.confidence is None
        # Round-trip still works.
        loaded = load_incident(inc.incident_id, session=session)
        assert loaded == inc
    finally:
        session.close()


def test_list_incidents_filters_and_orders(session_factory):
    session = session_factory()
    try:
        older = _sample_incident("inc-old").model_copy(
            update={"created_at": datetime(2026, 5, 20, 0, 0, tzinfo=UTC)}
        )
        newer = _sample_incident("inc-new").model_copy(
            update={
                "created_at": datetime(2026, 5, 21, 0, 0, tzinfo=UTC),
                "failing_model": "stg_customers",
            }
        )
        save_incident(older, session=session)
        save_incident(newer, session=session)
        session.commit()
        all_incidents = list_incidents(session=session)
        assert [i.incident_id for i in all_incidents] == ["inc-new", "inc-old"]
        only_orders = list_incidents(failing_model="stg_orders", session=session)
        assert [i.incident_id for i in only_orders] == ["inc-old"]
        only_auto = list_incidents(verdict_type=Verdict.AUTO, session=session)
        assert len(only_auto) == 2
    finally:
        session.close()


def test_delete_incident(session_factory):
    session = session_factory()
    try:
        inc = _sample_incident()
        save_incident(inc, session=session)
        session.commit()
        assert delete_incident(inc.incident_id, session=session) is True
        session.commit()
        assert load_incident(inc.incident_id, session=session) is None
        assert delete_incident(inc.incident_id, session=session) is False
    finally:
        session.close()


def test_load_unknown_incident_returns_none(session_factory):
    session = session_factory()
    try:
        assert load_incident("nope", session=session) is None
    finally:
        session.close()


def test_ground_truth_roundtrip(session_factory):
    session = session_factory()
    try:
        gt = _sample_ground_truth()
        save_ground_truth(gt, session=session)
        session.commit()
        loaded = list(iter_ground_truths(session=session))
        assert loaded == [gt]
    finally:
        session.close()


def test_ground_truth_upsert(session_factory):
    session = session_factory()
    try:
        gt = _sample_ground_truth()
        save_ground_truth(gt, session=session)
        save_ground_truth(gt.model_copy(update={"notes": "rerun"}), session=session)
        session.commit()
        rows = session.query(GroundTruthRow).all()
        assert len(rows) == 1
        assert rows[0].notes == "rerun"
    finally:
        session.close()
