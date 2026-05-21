"""Rules-based classifier (Week 3).

This module replaces the Week-2 tautology where ``Prediction.cause_class``
was read off the Fault object. Now an :class:`Classifier` ranks
:class:`ClassScore` candidates from upstream-stats evidence; the runner
takes the top-1 as the predicted cause class.

The classifier is pure-Python, deterministic, and uses **zero** LLM calls.
A Week-3.5 LLM tiebreaker hooks in via :class:`Classifier`'s ``tiebreaker``
constructor argument but is not built today.
"""

from dq_triage.classification.classifier import Classifier, classify
from dq_triage.classification.rules import ALL_DETECTORS, Detector

__all__ = ["ALL_DETECTORS", "Classifier", "Detector", "classify"]
