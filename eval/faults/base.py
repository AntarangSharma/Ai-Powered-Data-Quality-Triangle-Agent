"""Fault interface.

Contract:
  fault.apply(con, dataset_name, seed)
    1. mutates a raw table in `con` (a DuckDB connection)
    2. returns FaultResult with the GroundTruth label

A Fault must be:
  - DETERMINISTIC given `seed`
  - LOCALISED — mutate exactly one table+column to keep ground truth crisp
  - REVERSIBLE via snapshotting (the runner handles snapshot/restore)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import duckdb

from dq_triage.models import GroundTruth


@dataclass(frozen=True, slots=True)
class FaultResult:
    ground_truth: GroundTruth
    rows_affected: int


class Fault(ABC):
    """Base class. Subclasses set `pattern_id` and implement `apply`."""

    #: stable identifier, e.g. "null_spike.flat_5pct"
    pattern_id: str

    @abstractmethod
    def apply(
        self, con: duckdb.DuckDBPyConnection, dataset_name: str, seed: int
    ) -> FaultResult:
        """Mutate the warehouse in `con` and return ground truth."""

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Fault {self.pattern_id}>"
