"""Unit tests for eval.metrics. Written BEFORE the agent exists.

These tests pin the meaning of every metric. If a metric definition ever
shifts, the test breaks and the corresponding paragraph in the blog needs
to be re-checked too.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from dq_triage.models import GroundTruth, RootCauseClass
from eval.metrics import (
    Prediction,
    compute,
    expected_calibration_error,
    macro_class_f1,
    median_latency_seconds,
    offending_row_f1,
    offending_row_precision,
    offending_row_recall,
    pair,
    per_class_accuracy,
    top_k_table_accuracy,
)


def _gt(
    key: str,
    *,
    cls: RootCauseClass = RootCauseClass.UPSTREAM_NULL_SPIKE,
    table: str = "raw.orders",
    column: str | None = "cust_id",
    pks: tuple[str, ...] = ("1", "2", "3"),
) -> GroundTruth:
    return GroundTruth(
        incident_key=key,
        cause_class=cls,
        source_table=table,
        source_column=column,
        offending_row_pks=pks,
        injected_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
        fault_pattern="null_spike.flat_5pct",
    )


def _pred(
    key: str,
    *,
    cands: tuple[str, ...] = ("raw.orders",),
    column: str | None = "cust_id",
    pks: frozenset[str] = frozenset({"1", "2", "3"}),
    cls: RootCauseClass = RootCauseClass.UPSTREAM_NULL_SPIKE,
    confidence: float = 0.9,
    latency: float = 1.0,
) -> Prediction:
    return Prediction(
        incident_key=key,
        candidate_tables=cands,
        blame_column=column,
        blame_row_pks=pks,
        cause_class=cls,
        confidence=confidence,
        latency_seconds=latency,
    )


# --- pairing -------------------------------------------------------------


def test_pair_inner_joins_on_key() -> None:
    preds = [_pred("a"), _pred("b")]
    truths = [_gt("a"), _gt("c")]
    paired = pair(preds, truths)
    assert len(paired) == 1
    assert paired[0][1].incident_key == "a"


# --- top-k table accuracy -----------------------------------------------


def test_top1_perfect() -> None:
    pairs = [(_pred("a"), _gt("a"))]
    assert top_k_table_accuracy(pairs, k=1) == 1.0


def test_top1_miss_top3_hit() -> None:
    p = _pred("a", cands=("wrong1", "wrong2", "raw.orders"))
    pairs = [(p, _gt("a"))]
    assert top_k_table_accuracy(pairs, k=1) == 0.0
    assert top_k_table_accuracy(pairs, k=3) == 1.0


def test_top_k_empty_returns_zero() -> None:
    assert top_k_table_accuracy([], k=1) == 0.0


# --- row recall / precision / f1 ----------------------------------------


def test_row_recall_full() -> None:
    pairs = [(_pred("a", pks=frozenset({"1", "2", "3"})), _gt("a"))]
    assert offending_row_recall(pairs) == 1.0


def test_row_recall_half() -> None:
    pairs = [(_pred("a", pks=frozenset({"1"})), _gt("a"))]
    assert offending_row_recall(pairs) == pytest.approx(1 / 3)


def test_row_precision_penalises_spam() -> None:
    pairs = [(_pred("a", pks=frozenset({"1", "99", "100"})), _gt("a"))]
    # one of three predicted is in the GT set
    assert offending_row_precision(pairs) == pytest.approx(1 / 3)


def test_row_f1_zero_when_disjoint() -> None:
    pairs = [(_pred("a", pks=frozenset({"99"})), _gt("a"))]
    assert offending_row_f1(pairs) == 0.0


# --- class F1 / per-class -----------------------------------------------


def test_macro_f1_all_correct() -> None:
    pairs = [
        (_pred("a", cls=RootCauseClass.UPSTREAM_NULL_SPIKE), _gt("a", cls=RootCauseClass.UPSTREAM_NULL_SPIKE)),
        (_pred("b", cls=RootCauseClass.DUPLICATE_INGESTION), _gt("b", cls=RootCauseClass.DUPLICATE_INGESTION)),
    ]
    assert macro_class_f1(pairs) == 1.0


def test_macro_f1_all_wrong() -> None:
    pairs = [
        (_pred("a", cls=RootCauseClass.DUPLICATE_INGESTION), _gt("a", cls=RootCauseClass.UPSTREAM_NULL_SPIKE)),
        (_pred("b", cls=RootCauseClass.UPSTREAM_NULL_SPIKE), _gt("b", cls=RootCauseClass.DUPLICATE_INGESTION)),
    ]
    assert macro_class_f1(pairs) == 0.0


def test_per_class_accuracy() -> None:
    pairs = [
        (_pred("a", cls=RootCauseClass.UPSTREAM_NULL_SPIKE), _gt("a", cls=RootCauseClass.UPSTREAM_NULL_SPIKE)),
        (_pred("b", cls=RootCauseClass.DUPLICATE_INGESTION), _gt("b", cls=RootCauseClass.UPSTREAM_NULL_SPIKE)),
    ]
    acc = per_class_accuracy(pairs)
    assert acc[RootCauseClass.UPSTREAM_NULL_SPIKE] == 0.5


# --- ECE -----------------------------------------------------------------


def test_ece_perfect_calibration_is_zero() -> None:
    # Confidence 1.0 and always correct → ECE 0.
    pairs = [(_pred(f"k{i}", confidence=1.0), _gt(f"k{i}")) for i in range(10)]
    assert expected_calibration_error(pairs) == pytest.approx(0.0)


def test_ece_overconfident_is_positive() -> None:
    # Confidence 1.0 but always wrong → ECE 1.
    pairs = [
        (
            _pred(f"k{i}", confidence=1.0, cls=RootCauseClass.DUPLICATE_INGESTION),
            _gt(f"k{i}", cls=RootCauseClass.UPSTREAM_NULL_SPIKE),
        )
        for i in range(10)
    ]
    assert expected_calibration_error(pairs) == pytest.approx(1.0)


# --- latency -------------------------------------------------------------


def test_median_latency_odd() -> None:
    pairs = [(_pred(f"k{i}", latency=lat), _gt(f"k{i}")) for i, lat in enumerate([1, 3, 2])]
    assert median_latency_seconds(pairs) == 2.0


def test_median_latency_even() -> None:
    pairs = [(_pred(f"k{i}", latency=lat), _gt(f"k{i}")) for i, lat in enumerate([1, 3, 2, 4])]
    assert median_latency_seconds(pairs) == 2.5


# --- composite report ---------------------------------------------------


def test_compute_returns_report_with_markdown_row() -> None:
    pairs = [(_pred("a"), _gt("a"))]
    report = compute(pairs)
    assert report.n_incidents == 1
    assert report.top1_table_acc == 1.0
    row = report.as_markdown_row("Agent")
    assert "Agent" in row
    assert "100.0%" in row
