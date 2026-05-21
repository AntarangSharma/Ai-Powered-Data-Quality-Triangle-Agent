"""Null-spike faults: introduce NULLs into a column to break a downstream
`not_null` (or `relationships`) test.

Three patterns:
  1. flat_5pct       — flip 5% of values to NULL at random
  2. heavy_30pct     — flip 30% of values to NULL (catastrophic)
  3. conditional     — flip only rows matching a predicate (subtler)

A fault is bound at construction time to a `FaultTarget` from the dataset
config — so the same code drives Jaffle Shop, TPC-H, NYC-taxi, etc.
"""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timezone
from typing import ClassVar

import duckdb

from dq_triage.models import GroundTruth, RootCauseClass
from eval.datasets.jaffle_shop import FaultTarget
from eval.faults.base import Fault, FaultResult


def _incident_key(dataset: str, pattern: str, seed: int) -> str:
    return hashlib.sha256(f"{dataset}|{pattern}|{seed}".encode()).hexdigest()[:16]


class _NullSpikeBase(Fault):
    """Shared mechanic. Subclasses set `fraction` + optional `predicate_sql`."""

    cause_class = RootCauseClass.UPSTREAM_NULL_SPIKE
    fraction: ClassVar[float] = 0.05
    predicate_sql: ClassVar[str | None] = None

    def __init__(self, target: FaultTarget) -> None:
        self.target = target

    def apply(
        self, con: duckdb.DuckDBPyConnection, dataset_name: str, seed: int
    ) -> FaultResult:
        rng = random.Random(seed)
        t = self.target
        where = f"WHERE {self.predicate_sql}" if self.predicate_sql else ""
        rows = con.execute(
            f"SELECT {t.pk_column} FROM {t.raw_table} {where}"
        ).fetchall()
        candidate_pks = [r[0] for r in rows]
        if not candidate_pks:
            raise RuntimeError(
                f"Fault {self.pattern_id}: no candidate rows for "
                f"{t.raw_table}.{t.column}"
            )
        n_flip = max(1, int(len(candidate_pks) * self.fraction))
        chosen = rng.sample(candidate_pks, n_flip)

        placeholders = ",".join(["?"] * len(chosen))
        con.execute(
            f"UPDATE {t.raw_table} SET {t.column} = NULL "
            f"WHERE {t.pk_column} IN ({placeholders})",
            chosen,
        )

        gt = GroundTruth(
            incident_key=_incident_key(dataset_name, self.pattern_id, seed),
            cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
            source_table=t.raw_table,
            source_column=t.column,
            offending_row_pks=tuple(str(pk) for pk in sorted(chosen)),
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
    pattern_id = "null_spike.conditional_status_returned"
    fraction = 0.50
    predicate_sql = "status = 'returned'"


ALL_NULL_SPIKE_PATTERNS = [
    NullSpikeFlat5pct,
    NullSpikeHeavy30pct,
    NullSpikeConditional,
]
