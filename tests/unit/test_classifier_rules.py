"""Unit tests for the rules classifier.

Pure functions over :class:`ClassifierEvidence` — no warehouse needed.
"""

from __future__ import annotations

from itertools import pairwise

from dq_triage.agent.evidence import ClassifierEvidence
from dq_triage.classification import Classifier, classify
from dq_triage.classification.rules import (
    detect_broken_join_dropout,
    detect_duplicate_ingestion,
    detect_upstream_null_spike,
)
from dq_triage.models import RootCauseClass


def _ev(**overrides) -> ClassifierEvidence:
    base = {
        "failing_test_kind": "not_null",
        "failing_model": "stg_orders",
        "failing_column": "customer_id",
        "blame_model": "raw_orders",
        "blame_column": "user_id",
        "blame_pk_column": "id",
        "blame_row_count": 1000,
    }
    base.update(overrides)
    return ClassifierEvidence(**base)


# --- Detector: null spike -------------------------------------------------


def test_null_spike_below_threshold_is_silent():
    assert detect_upstream_null_spike(_ev(blame_null_rate=0.0005)) is None


def test_null_spike_scores_proportional_to_rate():
    score = detect_upstream_null_spike(_ev(blame_null_rate=0.05))
    assert score is not None
    assert score.cause_class is RootCauseClass.UPSTREAM_NULL_SPIKE
    assert 0.45 < score.score <= 0.55  # ~ 0.05 * 10


def test_null_spike_saturates_at_one():
    score = detect_upstream_null_spike(_ev(blame_null_rate=0.5))
    assert score is not None
    assert score.score == 1.0


def test_null_spike_dampened_when_orphans_present_and_rate_small():
    score = detect_upstream_null_spike(
        _ev(blame_null_rate=0.02, orphan_fk_count=10)
    )
    assert score is not None
    assert score.score < 0.2  # 0.02*10*0.5 = 0.1


def test_null_spike_not_dampened_when_rate_dominates():
    score = detect_upstream_null_spike(
        _ev(blame_null_rate=0.20, orphan_fk_count=10)
    )
    assert score is not None
    assert score.score == 1.0


# --- Detector: duplicate ingestion ---------------------------------------


def test_dupe_zero_is_silent():
    assert detect_duplicate_ingestion(_ev(blame_pk_dupe_count=0)) is None


def test_dupe_one_floors_at_0_7():
    score = detect_duplicate_ingestion(
        _ev(blame_pk_dupe_count=1, blame_row_count=1000)
    )
    assert score is not None
    assert score.cause_class is RootCauseClass.DUPLICATE_INGESTION
    assert score.score >= 0.7


def test_dupe_score_scales_with_rate():
    low = detect_duplicate_ingestion(
        _ev(blame_pk_dupe_count=1, blame_row_count=1000)
    )
    high = detect_duplicate_ingestion(
        _ev(blame_pk_dupe_count=100, blame_row_count=1000)
    )
    assert low is not None and high is not None
    assert high.score > low.score


# --- Detector: broken join dropout ---------------------------------------


def test_broken_join_zero_orphans_is_silent():
    assert detect_broken_join_dropout(_ev(orphan_fk_count=0)) is None


def test_broken_join_with_low_null_rate_scores_high():
    score = detect_broken_join_dropout(
        _ev(orphan_fk_count=5, blame_null_rate=0.0, blame_row_count=1000)
    )
    assert score is not None
    assert score.cause_class is RootCauseClass.BROKEN_JOIN_DROPOUT
    assert score.score >= 0.6


def test_broken_join_dampened_when_null_rate_high():
    score = detect_broken_join_dropout(
        _ev(orphan_fk_count=5, blame_null_rate=0.30, blame_row_count=1000)
    )
    assert score is not None
    assert score.score == 0.3


# --- Classifier aggregator ------------------------------------------------


def test_classifier_returns_unknown_when_no_signal():
    scores = classify(_ev())
    assert scores[0].cause_class is RootCauseClass.UNKNOWN
    assert len(scores) == 1


def test_classifier_orders_by_score_desc():
    ev = _ev(
        blame_null_rate=0.10,  # null_spike ~ 1.0
        blame_pk_dupe_count=5,  # dupe ~ 0.85
        orphan_fk_count=2,  # join_dropout dampened (null rate dominates) -> 0.3
    )
    scores = classify(ev)
    assert scores[0].cause_class is RootCauseClass.UPSTREAM_NULL_SPIKE
    # The other two are still present.
    classes = [s.cause_class for s in scores]
    assert RootCauseClass.DUPLICATE_INGESTION in classes
    assert RootCauseClass.BROKEN_JOIN_DROPOUT in classes
    # Strictly decreasing scores.
    for a, b in pairwise(scores):
        assert a.score >= b.score


def test_classifier_picks_join_dropout_over_null_when_no_nulls():
    ev = _ev(blame_null_rate=0.0, orphan_fk_count=4, blame_row_count=1000)
    scores = classify(ev)
    assert scores[0].cause_class is RootCauseClass.BROKEN_JOIN_DROPOUT


def test_classifier_picks_dupe_when_only_dupes_present():
    ev = _ev(blame_pk_dupe_count=3, blame_null_rate=0.0, orphan_fk_count=0)
    scores = classify(ev)
    assert scores[0].cause_class is RootCauseClass.DUPLICATE_INGESTION


def test_classifier_tiebreaker_fires_when_top1_below_threshold():
    fired: list[bool] = []

    def tiebreaker(_evidence, ranked):
        fired.append(True)
        return ranked

    clf = Classifier(tiebreaker=tiebreaker)
    # No signal → unknown @ 0.5 → top-1 < 0.7 → tiebreaker fires.
    clf.classify(_ev())
    assert fired == [True]


def test_classifier_tiebreaker_silent_when_confident():
    fired: list[bool] = []

    def tiebreaker(_evidence, ranked):
        fired.append(True)
        return ranked

    clf = Classifier(tiebreaker=tiebreaker)
    clf.classify(_ev(blame_pk_dupe_count=50))
    assert fired == []
