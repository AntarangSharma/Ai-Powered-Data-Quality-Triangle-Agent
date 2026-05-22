"""Rules-classifier aggregator.

Runs every detector, drops ``None``s, sorts by score descending, and exposes
a single :meth:`Classifier.classify` entry point. Top-1 is the predicted
cause class; the whole ranked tuple is preserved on
:class:`dq_triage.models.Incident` for explainability.

Tiebreaker hook
---------------
``Classifier(tiebreaker=fn)`` will (Week 3.5+) invoke an LLM when
top-1 score < 0.7 *or* top-1 minus top-2 < 0.1. The hook is left as a
keyword argument so the production code path is one diff away when API
credit allows.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from dq_triage.agent.evidence import ClassifierEvidence
from dq_triage.classification.rules import ALL_DETECTORS, Detector
from dq_triage.models import ClassScore, RootCauseClass

Tiebreaker = Callable[[ClassifierEvidence, tuple[ClassScore, ...]], tuple[ClassScore, ...]]

LOW_CONFIDENCE = 0.7
CLOSE_RACE = 0.1


class Classifier:
    """Runs detectors, returns ranked :class:`ClassScore` tuple."""

    def __init__(
        self,
        detectors: Sequence[Detector] | None = None,
        tiebreaker: Tiebreaker | None = None,
    ) -> None:
        self.detectors: tuple[Detector, ...] = (
            tuple(detectors) if detectors is not None else ALL_DETECTORS
        )
        self.tiebreaker = tiebreaker

    def classify(self, evidence: ClassifierEvidence) -> tuple[ClassScore, ...]:
        scored: list[ClassScore] = []
        for detector in self.detectors:
            result = detector(evidence)
            if result is not None:
                scored.append(result)
        if not scored:
            scored.append(
                ClassScore(
                    cause_class=RootCauseClass.UNKNOWN,
                    score=0.5,
                    evidence_keys=(),
                )
            )
        scored.sort(key=lambda s: s.score, reverse=True)
        ranked = tuple(scored)

        # Tiebreaker triggers when top-1 is shaky.
        if self.tiebreaker is not None and self._is_uncertain(ranked):
            ranked = self.tiebreaker(evidence, ranked)
        return ranked

    @staticmethod
    def _is_uncertain(ranked: tuple[ClassScore, ...]) -> bool:
        if not ranked:
            return True
        if ranked[0].score < LOW_CONFIDENCE:
            return True
        return len(ranked) >= 2 and (ranked[0].score - ranked[1].score) < CLOSE_RACE


def classify(evidence: ClassifierEvidence) -> tuple[ClassScore, ...]:
    """Convenience: one-shot classification with the default ruleset."""
    return Classifier().classify(evidence)
