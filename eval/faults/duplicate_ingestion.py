"""Duplicate-ingestion faults: re-insert rows with existing PK values so the
downstream `unique` test breaks.

Real-world analogue: a Kafka consumer that double-commits, or an idempotency
key that wasn't actually idempotent. The warehouse ends up with two copies of
the same logical row.

Patterns:
  1. flat_1pct       — duplicate 1% of rows
  2. heavy_5pct      — duplicate 5%
  3. burst_recent    — duplicate the last 10% of rows (mimics a re-run window)

The duplicates carry identical values across all columns (including the PK).
The Attributor should land on `raw_orders.id` because the failing rows in
`stg_orders.order_id` come from raw_orders via a DIRECT projection.
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


class _DupeBase(Fault):
    """Shared mechanic for duplicate-ingestion faults."""

    cause_class = RootCauseClass.DUPLICATE_INGESTION
    fraction: ClassVar[float] = 0.01
    #: If True, pick the last N rows by PK (descending sort) instead of random.
    #: Mimics a "re-process the last window" type of re-ingestion bug.
    pick_tail: ClassVar[bool] = False

    def __init__(self, target: FaultTarget) -> None:
        self.target = target

    def apply(
        self, con: duckdb.DuckDBPyConnection, dataset_name: str, seed: int
    ) -> FaultResult:
        rng = random.Random(seed)
        t = self.target
        # 1. Pick the PK values whose rows we'll duplicate.
        all_pks = [r[0] for r in con.execute(
            f"SELECT {t.pk_column} FROM {t.raw_table} ORDER BY {t.pk_column}"
        ).fetchall()]
        if not all_pks:
            raise RuntimeError(f"{self.pattern_id}: {t.raw_table} is empty")
        n_dup = max(1, int(len(all_pks) * self.fraction))
        if self.pick_tail:
            chosen = all_pks[-n_dup:]
        else:
            chosen = rng.sample(all_pks, n_dup)

        # 2. Re-insert those rows verbatim. We don't know the schema at compile
        #    time, so we copy whole rows via INSERT…SELECT…WHERE pk IN (…).
        placeholders = ",".join(["?"] * len(chosen))
        con.execute(
            f"INSERT INTO {t.raw_table} "
            f"SELECT * FROM {t.raw_table} WHERE {t.pk_column} IN ({placeholders})",
            chosen,
        )

        gt = GroundTruth(
            incident_key=_incident_key(dataset_name, self.pattern_id, seed),
            cause_class=self.cause_class,
            source_table=t.raw_table,
            source_column=t.column,
            offending_row_pks=tuple(str(pk) for pk in sorted(chosen)),
            injected_at=datetime.now(timezone.utc),
            fault_pattern=self.pattern_id,
            notes=f"duplicated {n_dup} rows in {t.raw_table}.{t.pk_column}",
        )
        return FaultResult(ground_truth=gt, rows_affected=n_dup)


class DuplicateIngestionFlat1pct(_DupeBase):
    pattern_id = "duplicate_ingestion.flat_1pct"
    fraction = 0.01


class DuplicateIngestionHeavy5pct(_DupeBase):
    pattern_id = "duplicate_ingestion.heavy_5pct"
    fraction = 0.05


class DuplicateIngestionBurstRecent(_DupeBase):
    pattern_id = "duplicate_ingestion.burst_recent_10pct"
    fraction = 0.10
    pick_tail = True


ALL_DUPE_PATTERNS = [
    DuplicateIngestionFlat1pct,
    DuplicateIngestionHeavy5pct,
    DuplicateIngestionBurstRecent,
]
