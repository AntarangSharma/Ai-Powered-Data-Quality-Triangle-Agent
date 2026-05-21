"""Deterministic rule detectors.

Each detector is a pure function ``ClassifierEvidence -> ClassScore | None``.
``None`` means "no signal for this class"; the classifier filters those out.
Scores live in [0, 1]; higher = more confident this is the cause class.

Calibration choices (held in one place for review):

* ``NULL_RATE_THRESHOLD = 0.001`` — anything ≥ 0.1% on a column that should
  be ``not_null`` counts as a spike. Raw seeds in our benchmark start at
  exactly 0.0 nulls in the tested columns, so 0.001 is a generous noise
  floor and won't trigger on background data.
* The null-spike score saturates at 10% nulls (``* 10``, clamped to 1.0).
* Duplicate-ingestion baseline score is 0.7 the moment a single PK
  duplicate is observed; +30 * dupe-rate raises it toward 1.0 fast.
* Broken-join-dropout requires orphan FKs **and** a low null rate on the
  same column — high null-rate explains the orphan more parsimoniously as a
  null-spike, so dropout score is dampened to 0.3 in that case.
"""

from __future__ import annotations

from collections.abc import Callable

from dq_triage.agent.evidence import ClassifierEvidence
from dq_triage.models import ClassScore, RootCauseClass

Detector = Callable[[ClassifierEvidence], "ClassScore | None"]

NULL_RATE_THRESHOLD = 0.001
NULL_RATE_DOMINATES = 0.05


def detect_upstream_null_spike(e: ClassifierEvidence) -> ClassScore | None:
    """Triggers when the blame column has more nulls than baseline noise.

    Score scales linearly with null rate up to 10%. If orphan FKs are also
    present *and* null rate is small, the rule defers to the join-dropout
    detector by halving the score (the union of evidence is rare and we
    want the more specific classifier to win)."""
    if e.blame_null_rate <= NULL_RATE_THRESHOLD:
        return None
    raw = min(1.0, e.blame_null_rate * 10.0)
    if e.orphan_fk_count > 0 and e.blame_null_rate < NULL_RATE_DOMINATES:
        raw *= 0.5
    return ClassScore(
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
        score=raw,
        evidence_keys=("blame_null_rate",),
    )


def detect_duplicate_ingestion(e: ClassifierEvidence) -> ClassScore | None:
    """Triggers when the PK column has any duplicated value.

    A single dupe is a definitive uniqueness violation, so the score floors
    at 0.7 and scales with the dupe rate (extra rows / total rows)."""
    if e.blame_pk_dupe_count <= 0:
        return None
    dupe_rate = e.blame_pk_dupe_count / max(e.blame_row_count, 1)
    score = min(1.0, 0.7 + dupe_rate * 30.0)
    return ClassScore(
        cause_class=RootCauseClass.DUPLICATE_INGESTION,
        score=score,
        evidence_keys=("blame_pk_dupe_count",),
    )


def detect_broken_join_dropout(e: ClassifierEvidence) -> ClassScore | None:
    """Triggers when the blame column has orphan FK values.

    If the column *also* has a high null rate, that's a more parsimonious
    explanation (null-spike) and the dropout score is dampened to 0.3 so the
    null-spike detector wins the top-1."""
    if e.orphan_fk_count <= 0:
        return None
    if e.blame_null_rate >= NULL_RATE_DOMINATES:
        return ClassScore(
            cause_class=RootCauseClass.BROKEN_JOIN_DROPOUT,
            score=0.3,
            evidence_keys=("orphan_fk_count", "blame_null_rate"),
        )
    rate = e.orphan_fk_count / max(e.blame_row_count, 1)
    score = min(1.0, 0.6 + rate * 20.0)
    return ClassScore(
        cause_class=RootCauseClass.BROKEN_JOIN_DROPOUT,
        score=score,
        evidence_keys=("orphan_fk_count",),
    )


ALL_DETECTORS: tuple[Detector, ...] = (
    detect_upstream_null_spike,
    detect_duplicate_ingestion,
    detect_broken_join_dropout,
)
