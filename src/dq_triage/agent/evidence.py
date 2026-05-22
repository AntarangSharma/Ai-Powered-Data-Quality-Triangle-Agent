"""Evidence assembly for the rules classifier.

Takes:
  * the failing test (kind + model + column)
  * the Attributor's BlameLocation (raw_*-level model + column)
  * optional parent raw relation (for relationships-test fault families)

…and runs a handful of stats probes to produce a single
:class:`ClassifierEvidence` snapshot. The classifier reads only this — it
never touches DuckDB itself, which keeps the rules trivially unit-testable.

Notes
-----
The probes are *defensive*: any failure (missing column, type mismatch,
empty table) collapses to a benign default rather than raising. That makes
the assembler safe to call on partial / corrupt warehouses; the classifier
can decide what "no signal" means.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import duckdb

from dq_triage.stats.probes import (
    probe_column_stats,
    probe_dupe_count,
    probe_row_count,
)


@dataclass(frozen=True, slots=True)
class ClassifierEvidence:
    """Compact, classifier-internal view over upstream stats.

    All numeric fields use *neutral* defaults (0 / 0.0 / None) for missing
    signal so detector rules can branch on ``> 0`` / ``> threshold`` without
    null-checks.
    """

    failing_test_kind: str  # "not_null" | "unique" | "relationships" | "unknown"
    failing_model: str
    failing_column: str | None

    blame_model: str
    blame_column: str | None
    blame_pk_column: str

    blame_row_count: int = 0
    blame_null_rate: float = 0.0
    blame_cardinality_ratio: float = 0.0
    blame_pk_dupe_count: int = 0  # extra rows beyond first per duplicated PK

    # Relationships-only:
    parent_table: str | None = None
    parent_pk_column: str | None = None
    parent_row_count: int | None = None
    orphan_fk_count: int = 0


def _safe_int(fn: Callable[[], int | float], default: int = 0) -> int:
    try:
        return int(fn())
    except Exception:
        return default


def assemble_evidence(
    con: duckdb.DuckDBPyConnection,
    *,
    failing_test_kind: str,
    failing_model: str,
    failing_column: str | None,
    blame_model: str,
    blame_column: str | None,
    blame_pk_column: str,
    parent_table: str | None = None,
    parent_pk_column: str | None = None,
) -> ClassifierEvidence:
    """Probe the warehouse and return a :class:`ClassifierEvidence` snapshot."""

    blame_row_count = _safe_int(lambda: probe_row_count(con, blame_model))

    blame_null_rate = 0.0
    blame_cardinality_ratio = 0.0
    if blame_column:
        try:
            stats = probe_column_stats(con, blame_model, blame_column)
            blame_null_rate = stats.null_rate
            blame_cardinality_ratio = stats.cardinality_ratio
        except Exception:
            pass

    # Dupe count on the PK (extra rows beyond the first per duplicated value).
    blame_pk_dupe_count = 0
    try:
        dupes = probe_dupe_count(con, blame_model, (blame_pk_column,))
        blame_pk_dupe_count = sum(max(0, d.count - 1) for d in dupes)
    except Exception:
        pass

    parent_row_count: int | None = None
    orphan_fk_count = 0
    if failing_test_kind == "relationships" and parent_table and parent_pk_column and blame_column:
        parent_row_count = _safe_int(
            lambda: probe_row_count(con, parent_table),
            default=0,
        )
        try:
            # Validated identifiers (same regex used by stats.probes).
            from dq_triage.stats.probes import _quote_ident, _quote_relation

            sql = (
                f"SELECT COUNT(*) FROM {_quote_relation(blame_model)} c "
                f"WHERE c.{_quote_ident(blame_column)} IS NOT NULL "
                f"  AND c.{_quote_ident(blame_column)} NOT IN ("
                f"    SELECT {_quote_ident(parent_pk_column)} "
                f"    FROM {_quote_relation(parent_table)}"
                f"  )"
            )
            (orphan_fk_count,) = con.execute(sql).fetchone()  # type: ignore[misc]
            orphan_fk_count = int(orphan_fk_count)
        except Exception:
            orphan_fk_count = 0

    return ClassifierEvidence(
        failing_test_kind=failing_test_kind,
        failing_model=failing_model,
        failing_column=failing_column,
        blame_model=blame_model,
        blame_column=blame_column,
        blame_pk_column=blame_pk_column,
        blame_row_count=blame_row_count,
        blame_null_rate=blame_null_rate,
        blame_cardinality_ratio=blame_cardinality_ratio,
        blame_pk_dupe_count=blame_pk_dupe_count,
        parent_table=parent_table,
        parent_pk_column=parent_pk_column,
        parent_row_count=parent_row_count,
        orphan_fk_count=orphan_fk_count,
    )
