"""Agent-level orchestration: evidence assembly, classification glue.

Importing :mod:`dq_triage.agent.evidence` is enough for the rules
classifier; richer LLM-payload assembly (token-budgeted EvidenceBundle)
lives behind a separate entry point in Week 3+.
"""

from dq_triage.agent.evidence import ClassifierEvidence, assemble_evidence

__all__ = ["ClassifierEvidence", "assemble_evidence"]
