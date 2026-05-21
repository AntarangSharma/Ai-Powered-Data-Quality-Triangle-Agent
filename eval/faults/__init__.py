"""Fault injection patterns.

Each Fault is a controlled mutation of a raw source table that produces a
known downstream DQ failure and a fully-specified GroundTruth.
"""

from eval.faults.base import Fault, FaultResult

__all__ = ["Fault", "FaultResult"]
