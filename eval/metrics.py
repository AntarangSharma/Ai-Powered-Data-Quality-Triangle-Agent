"""Metric functions for the DQ Triage benchmark.

Every metric is a pure function over (predictions, ground_truths). No I/O.
This module is tested in isolation (`tests/unit/test_metrics.py`) BEFORE any
agent code exists, because the metrics define what 'correct' means.

Predictions and ground truths share an `incident_key` so we can join them.

Reference formulas (see docs/02_revised_plan.md §Metrics):
  - top_k_table_accuracy
  - column_accuracy_given_table
  - offending_row_recall / precision / f1
  - macro_class_f1
  - ece (expected calibration error)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean
from typing import Iterable, Sequence

from dq_triage.models import GroundTruth, Incident, RootCauseClass

# ---------------------------------------------------------------------------
# Prediction view
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Prediction:
    """A flat view over an Incident used for scoring.

    Baselines that don't produce full Incidents can construct Predictions
    directly so they're scored on the same metrics.
    """

    incident_key: str
    candidate_tables: tuple[str, ...]  # ordered, best-first
    blame_column: str | None
    blame_row_pks: frozenset[str]
    cause_class: RootCauseClass
    confidence: float  # calibrated [0,1] if available else raw
    latency_seconds: float

    @classmethod
    def from_incident(cls, incident_key: str, incident: Incident) -> "Prediction":
        cands: list[str] = [incident.blame_location.model]
        for h in incident.hypotheses:
            if h.blame_model not in cands:
                cands.append(h.blame_model)
        verdict_cls = (
            incident.final_verdict.cause_class
            if incident.final_verdict is not None
            else incident.hypotheses[0].cause_class
        )
        confidence = (
            incident.final_verdict.confidence_calibrated
            if incident.final_verdict is not None
            else incident.hypotheses[0].confidence_calibrated
        )
        return cls(
            incident_key=incident_key,
            candidate_tables=tuple(cands),
            blame_column=incident.blame_location.column,
            blame_row_pks=frozenset(incident.blame_location.row_pks),
            cause_class=verdict_cls,
            confidence=confidence,
            latency_seconds=incident.time_to_verdict_seconds,
        )


# ---------------------------------------------------------------------------
# Pairing
# ---------------------------------------------------------------------------


def pair(
    predictions: Iterable[Prediction], truths: Iterable[GroundTruth]
) -> list[tuple[Prediction, GroundTruth]]:
    """Inner-join on incident_key. Missing predictions are dropped (and logged
    by the caller). Strict pairing — every paired GT must have a prediction."""
    p_map = {p.incident_key: p for p in predictions}
    pairs: list[tuple[Prediction, GroundTruth]] = []
    for gt in truths:
        if gt.incident_key in p_map:
            pairs.append((p_map[gt.incident_key], gt))
    return pairs


# ---------------------------------------------------------------------------
# Attribution metrics
# ---------------------------------------------------------------------------


def top_k_table_accuracy(
    pairs: Sequence[tuple[Prediction, GroundTruth]], k: int = 1
) -> float:
    if not pairs:
        return 0.0
    hits = sum(1 for p, g in pairs if g.source_table in p.candidate_tables[:k])
    return hits / len(pairs)


def column_accuracy_given_table(
    pairs: Sequence[tuple[Prediction, GroundTruth]],
) -> float:
    """Among incidents where top-1 table is correct, how often is the column correct?"""
    eligible = [
        (p, g) for p, g in pairs if p.candidate_tables and p.candidate_tables[0] == g.source_table
    ]
    if not eligible:
        return 0.0
    hits = sum(
        1 for p, g in eligible if (p.blame_column is not None and p.blame_column == g.source_column)
    )
    return hits / len(eligible)


def offending_row_recall(pairs: Sequence[tuple[Prediction, GroundTruth]]) -> float:
    if not pairs:
        return 0.0
    per_incident: list[float] = []
    for p, g in pairs:
        gt_pks = frozenset(g.offending_row_pks)
        if not gt_pks:
            continue
        inter = len(p.blame_row_pks & gt_pks)
        per_incident.append(inter / len(gt_pks))
    return mean(per_incident) if per_incident else 0.0


def offending_row_precision(pairs: Sequence[tuple[Prediction, GroundTruth]]) -> float:
    if not pairs:
        return 0.0
    per_incident: list[float] = []
    for p, g in pairs:
        gt_pks = frozenset(g.offending_row_pks)
        if not p.blame_row_pks:
            continue
        inter = len(p.blame_row_pks & gt_pks)
        per_incident.append(inter / len(p.blame_row_pks))
    return mean(per_incident) if per_incident else 0.0


def offending_row_f1(pairs: Sequence[tuple[Prediction, GroundTruth]]) -> float:
    r = offending_row_recall(pairs)
    p = offending_row_precision(pairs)
    if r + p == 0:
        return 0.0
    return 2 * r * p / (r + p)


# ---------------------------------------------------------------------------
# Classification metrics
# ---------------------------------------------------------------------------


def macro_class_f1(pairs: Sequence[tuple[Prediction, GroundTruth]]) -> float:
    """Macro-averaged F1 across all classes present in ground truth.

    Implemented without sklearn so this module has zero runtime deps beyond stdlib.
    """
    if not pairs:
        return 0.0
    classes = sorted({g.cause_class for _, g in pairs})
    f1s: list[float] = []
    for cls in classes:
        tp = sum(1 for p, g in pairs if p.cause_class == cls and g.cause_class == cls)
        fp = sum(1 for p, g in pairs if p.cause_class == cls and g.cause_class != cls)
        fn = sum(1 for p, g in pairs if p.cause_class != cls and g.cause_class == cls)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        f1s.append(f1)
    return mean(f1s) if f1s else 0.0


def per_class_accuracy(
    pairs: Sequence[tuple[Prediction, GroundTruth]],
) -> dict[RootCauseClass, float]:
    buckets: dict[RootCauseClass, list[int]] = defaultdict(list)
    for p, g in pairs:
        buckets[g.cause_class].append(1 if p.cause_class == g.cause_class else 0)
    return {cls: (sum(v) / len(v) if v else 0.0) for cls, v in buckets.items()}


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


def expected_calibration_error(
    pairs: Sequence[tuple[Prediction, GroundTruth]], n_bins: int = 10
) -> float:
    """ECE = Σ_b (n_b / N) × |acc(b) − conf(b)|.

    A binary correctness signal is used: prediction is 'correct' iff
    cause_class matches ground truth.
    """
    if not pairs:
        return 0.0
    n = len(pairs)
    bins: list[list[tuple[float, int]]] = [[] for _ in range(n_bins)]
    for p, g in pairs:
        c = max(0.0, min(1.0, p.confidence))
        idx = min(int(c * n_bins), n_bins - 1)
        correct = 1 if p.cause_class == g.cause_class else 0
        bins[idx].append((c, correct))
    ece = 0.0
    for bucket in bins:
        if not bucket:
            continue
        confs = [c for c, _ in bucket]
        accs = [a for _, a in bucket]
        ece += (len(bucket) / n) * abs(mean(accs) - mean(confs))
    return ece


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


def median_latency_seconds(pairs: Sequence[tuple[Prediction, GroundTruth]]) -> float:
    if not pairs:
        return 0.0
    xs = sorted(p.latency_seconds for p, _ in pairs)
    mid = len(xs) // 2
    if len(xs) % 2:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2


# ---------------------------------------------------------------------------
# Composite report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricsReport:
    n_incidents: int
    top1_table_acc: float
    top3_table_acc: float
    column_acc_given_table: float
    row_recall: float
    row_precision: float
    row_f1: float
    macro_class_f1: float
    per_class_accuracy: dict[RootCauseClass, float]
    ece: float
    median_latency_s: float

    def as_markdown_row(self, system_name: str) -> str:
        return (
            f"| {system_name} | {self.top1_table_acc:.1%} | {self.top3_table_acc:.1%} | "
            f"{self.column_acc_given_table:.1%} | {self.row_recall:.2f} | {self.row_f1:.2f} | "
            f"{self.macro_class_f1:.2f} | {self.median_latency_s:.1f}s | {self.ece:.3f} |"
        )

    @staticmethod
    def markdown_header() -> str:
        return (
            "| System | Top-1 table | Top-3 table | Col\\|table | Row recall | Row F1 | "
            "Macro F1 | Median latency | ECE |\n"
            "|---|---|---|---|---|---|---|---|---|"
        )


def compute(pairs: Sequence[tuple[Prediction, GroundTruth]]) -> MetricsReport:
    return MetricsReport(
        n_incidents=len(pairs),
        top1_table_acc=top_k_table_accuracy(pairs, k=1),
        top3_table_acc=top_k_table_accuracy(pairs, k=3),
        column_acc_given_table=column_accuracy_given_table(pairs),
        row_recall=offending_row_recall(pairs),
        row_precision=offending_row_precision(pairs),
        row_f1=offending_row_f1(pairs),
        macro_class_f1=macro_class_f1(pairs),
        per_class_accuracy=per_class_accuracy(pairs),
        ece=expected_calibration_error(pairs),
        median_latency_s=median_latency_seconds(pairs),
    )
