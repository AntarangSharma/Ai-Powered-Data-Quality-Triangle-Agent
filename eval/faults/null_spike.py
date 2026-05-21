"""Null-spike faults: introduce NULLs into an upstream column to violate a
downstream `not_null` test.

Three patterns (per the v1 plan §2.3):
  1. flat_5pct       — flip 5% of values to NULL at random
  2. heavy_30pct     — flip 30% of values to NULL (catastrophic)
  3. conditional     — flip only rows matching a predicate (subtle, harder)
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone
from typing import Final

import duckdb

from dq_triage.models import GroundTruth, RootCauseClass
from eval.faults.base import Fault, FaultResult


def _incident_key(dataset: str, pattern: str, seed: int) -> str:
    return hashlib.sha256(f"{dataset}|{pattern}|{seed}".encode()).hexdigest()[:16]


class _NullSpikeBase(Fault):
    """Shared machinery — subclasses set fraction + selector SQL."""

    fraction: float = 0.05
    predicate_sql: str | None = None  # extra WHERE clause; None means no filter
    target_table: Final[str] = "raw_customers"
    target_column: Final[str] = "c_nationkey"
    pk_column: Final[str] = "c_custkey"

    def apply(
        self, con: duckdb.DuckDBPyConnection, dataset_name: str, seed: int
    ) -> FaultResult:
        rng = random.Random(seed)
        # Sample PKs to flip.
        where = f"WHERE {self.predicate_sql}" if self.predicate_sql else ""
        candidate_pks = [
            row[0]
            for row in con.execute(
                f"SELECT {self.pk_column} FROM {self.target_table} {where}"
            ).fetchall()
        ]
        if not candidate_pks:
            raise RuntimeError(
                f"Fault {self.pattern_id}: no candidate rows for "
                f"{self.target_table}.{self.target_column}"
            )
        n_flip = max(1, int(len(candidate_pks) * self.fraction))
        chosen = rng.sample(candidate_pks, n_flip)

        # Apply mutation in a single UPDATE.
        placeholders = ",".join(["?"] * len(chosen))
        con.execute(
            f"UPDATE {self.target_table} "
            f"SET {self.target_column} = NULL "
            f"WHERE {self.pk_column} IN ({placeholders})",
            chosen,
        )

        gt = GroundTruth(
            incident_key=_incident_key(dataset_name, self.pattern_id, seed),
            cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
            source_table=self.target_table,
            source_column=self.target_column,
            offending_row_pks=tuple(str(pk) for pk in chosen),
            injected_at=datetime.now(timezone.utc),
            fault_pattern=self.pattern_id,
        )
        return FaultResult(ground_truth=gt, rows_affected=n_flip)


class NullSpikeFlat5pct(_NullSpikeBase):
    pattern_id = "null_spike.flat_5pct"
    fraction = 0.05


class NullSpikeHeavy30pct(_NullSpikeBase):
    pattern_id = "null_spike.heavy_30pct"
    fraction = 0.30


class NullSpikeConditional(_NullSpikeBase):
    """Flip ~10% but only from rows in a specific market segment — subtler,
    because the null pattern correlates with another column."""

    pattern_id = "null_spike.conditional"
    fraction = 0.10
    predicate_sql = "c_mktsegment = 'AUTOMOBILE'"


ALL_NULL_SPIKE_PATTERNS: Final[list[type[Fault]]] = [
    NullSpikeFlat5pct,
    NullSpikeHeavy30pct,
    NullSpikeConditional,
]
