"""Broken-join dropout faults: delete rows from a parent table that have
children referencing them. The downstream `relationships` test on the child
side fails because some FKs no longer point anywhere.

Real-world analogue: a hard-delete in a source CRM that wasn't propagated as
soft-delete, leaving orphaned references in downstream fact tables.

Patterns:
  1. flat_1pct        — drop 1% of referenced parents at random
  2. heavy_5pct       — drop 5%
  3. targeted_recent  — drop the last 5% of parents by PK (worst case: most
     of their children are recent and visible in failing-row samples)

Subtle but important: the ATTRIBUTOR's blame lands on the **child** table
(here `raw_orders.user_id`), because that's where the orphan FKs physically
live. The PARENT deletion (`raw_customers.id`) is the actual root cause but
identifying it requires the classifier to compare upstream stats (the
parent's row count dropped) — that's Week 3's job. We record the deletion
in `GroundTruth.notes` so the classifier eval can verify it.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, datetime
from typing import ClassVar

import duckdb

from dq_triage.models import GroundTruth, RootCauseClass
from eval.datasets.jaffle_shop import FaultTarget
from eval.faults.base import Fault, FaultResult


def _incident_key(dataset: str, pattern: str, seed: int) -> str:
    return hashlib.sha256(f"{dataset}|{pattern}|{seed}".encode()).hexdigest()[:16]


class _BrokenJoinBase(Fault):
    """Shared mechanic. Deletes rows from `target.raw_table` whose PK is
    referenced by `child_table.child_fk_column`.

    For Jaffle we hardcode the child relationship; future datasets will need
    to declare it on FaultTarget.
    """

    cause_class = RootCauseClass.BROKEN_JOIN_DROPOUT
    fraction: ClassVar[float] = 0.01
    pick_tail: ClassVar[bool] = False
    #: The child side of the relationship. (Hardcoded for Jaffle; promote to
    #: FaultTarget when we add a second dataset.)
    child_table: ClassVar[str] = "raw_orders"
    child_fk_column: ClassVar[str] = "user_id"
    child_pk_column: ClassVar[str] = "id"

    def __init__(self, target: FaultTarget) -> None:
        self.target = target

    def apply(self, con: duckdb.DuckDBPyConnection, dataset_name: str, seed: int) -> FaultResult:
        rng = random.Random(seed)
        t = self.target
        # 1. Find parent PKs that are actually referenced by the child.
        #    Deleting unreferenced parents wouldn't break any relationships
        #    test — we want a *symptomatic* fault.
        referenced = [
            r[0]
            for r in con.execute(
                f"SELECT DISTINCT p.{t.pk_column} "
                f"FROM {t.raw_table} p "
                f"WHERE p.{t.pk_column} IN ("
                f"  SELECT {self.child_fk_column} FROM {self.child_table} "
                f"  WHERE {self.child_fk_column} IS NOT NULL"
                f") "
                f"ORDER BY p.{t.pk_column}"
            ).fetchall()
        ]
        if not referenced:
            raise RuntimeError(
                f"{self.pattern_id}: no referenced rows to delete in {t.raw_table}.{t.pk_column}"
            )
        n_drop = max(1, int(len(referenced) * self.fraction))
        if self.pick_tail:
            chosen_parent_pks = referenced[-n_drop:]
        else:
            chosen_parent_pks = rng.sample(referenced, n_drop)

        # 2. Find the child rows that will become orphans (these are the
        #    Attributor's target).
        ph = ",".join(["?"] * len(chosen_parent_pks))
        orphan_rows = con.execute(
            f"SELECT {self.child_pk_column} FROM {self.child_table} "
            f"WHERE {self.child_fk_column} IN ({ph})",
            chosen_parent_pks,
        ).fetchall()
        orphan_child_pks = tuple(str(r[0]) for r in orphan_rows)

        # 3. Actually delete the parents.
        con.execute(
            f"DELETE FROM {t.raw_table} WHERE {t.pk_column} IN ({ph})",
            chosen_parent_pks,
        )

        gt = GroundTruth(
            incident_key=_incident_key(dataset_name, self.pattern_id, seed),
            cause_class=self.cause_class,
            # Source where the *symptom* lives — what the attributor lands on.
            source_table=self.child_table,
            source_column=self.child_fk_column,
            offending_row_pks=tuple(sorted(orphan_child_pks, key=lambda s: (len(s), s))),
            injected_at=datetime.now(UTC),
            fault_pattern=self.pattern_id,
            # Where the actual deletion happened (for classifier eval).
            notes=(f"deleted parents {sorted(chosen_parent_pks)} from {t.raw_table}.{t.pk_column}"),
        )
        return FaultResult(ground_truth=gt, rows_affected=len(orphan_child_pks))


class BrokenJoinDropoutFlat1pct(_BrokenJoinBase):
    pattern_id = "broken_join_dropout.flat_1pct"
    fraction = 0.01


class BrokenJoinDropoutHeavy5pct(_BrokenJoinBase):
    pattern_id = "broken_join_dropout.heavy_5pct"
    fraction = 0.05


class BrokenJoinDropoutTargetedRecent(_BrokenJoinBase):
    pattern_id = "broken_join_dropout.targeted_recent_5pct"
    fraction = 0.05
    pick_tail = True


ALL_BROKEN_JOIN_PATTERNS = [
    BrokenJoinDropoutFlat1pct,
    BrokenJoinDropoutHeavy5pct,
    BrokenJoinDropoutTargetedRecent,
]
