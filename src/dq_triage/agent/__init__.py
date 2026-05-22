"""Agent-level orchestration: evidence assembly, classification glue.

Importing :mod:`dq_triage.agent.evidence` is enough for the rules
classifier; richer LLM-payload assembly (token-budgeted EvidenceBundle)
lives behind a separate entry point in Week 3+.
"""

from dq_triage.agent.evidence import ClassifierEvidence, assemble_evidence
from dq_triage.agent.orchestrator import (
    FailingTest,
    load_failing_tests,
    triage,
    triage_and_persist,
)

__all__ = [
    "ClassifierEvidence",
    "FailingTest",
    "assemble_evidence",
    "load_failing_tests",
    "triage",
    "triage_and_persist",
]
