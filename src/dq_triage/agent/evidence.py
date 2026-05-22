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
from datetime import UTC, datetime

import duckdb

from dq_triage.stats.probes import (
    probe_column_dtype,
    probe_column_stats,
    probe_dtype_distribution,
    probe_dupe_count,
    probe_freshness,
    probe_numeric_moments,
    probe_row_count,
    probe_skew,
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

    # 7 New statistical fields for Tier 1 Closeout:
    blame_lag_ratio: float = 0.0
    blame_integer_compatible_ratio: float = 0.0
    blame_float_compatible_ratio: float = 0.0
    blame_dtype_mismatch: bool = False
    blame_join_fanout_risk: bool = False
    blame_value_drift_z: float = 0.0
    blame_stale_dimension: bool = False
    blame_anomaly_score: float = 0.0

    # Relationships-only:
    parent_table: str | None = None
    parent_pk_column: str | None = None
    parent_row_count: int | None = None
    orphan_fk_count: int = 0

    def canonical_json(self) -> str:
        import dataclasses
        import json

        return json.dumps(dataclasses.asdict(self), sort_keys=True)


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
    expected_dtype: str | None = None,
    expected_mean: float | None = None,
    expected_stddev: float | None = None,
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

    # 1. Probing blame_lag_ratio (late_arriving)
    blame_lag_ratio = 0.0
    try:
        parts = blame_model.split(".")
        if len(parts) == 1:
            schema_clause = ""
            params = [parts[0]]
        elif len(parts) == 2:
            schema_clause = "table_schema = ? AND "
            params = [parts[0], parts[1]]
        else:
            schema_clause = "table_schema = ? AND "
            params = [parts[1], parts[2]]

        sql = (
            "SELECT column_name FROM information_schema.columns "
            f"WHERE {schema_clause}table_name = ? AND ("
            "  data_type LIKE '%TIMESTAMP%' OR data_type LIKE '%DATE%' OR data_type LIKE '%TIME%'"
            ")"
        )
        rows = con.execute(sql, params).fetchall()
        if rows:
            ts_col = rows[0][0]
            fresh = probe_freshness(con, blame_model, ts_col, datetime.now(UTC))
            if fresh.lag_seconds is not None:
                blame_lag_ratio = (fresh.lag_seconds / 60.0) / 1440.0
    except Exception:
        pass

    # 2. Probing type compatibility (type_coercion)
    blame_integer_compatible_ratio = 0.0
    blame_float_compatible_ratio = 0.0
    if blame_column:
        try:
            ints, floats = probe_dtype_distribution(con, blame_model, blame_column)
            blame_integer_compatible_ratio = ints
            blame_float_compatible_ratio = floats
        except Exception:
            pass

    # 3. Probing type mismatch (source_schema_change)
    blame_dtype_mismatch = False
    if expected_dtype and blame_column:
        try:
            actual_dtype = probe_column_dtype(con, blame_model, blame_column)

            def norm_type(t: str) -> str:
                t = t.upper()
                if "VARCHAR" in t or "TEXT" in t or "STRING" in t:
                    return "STRING"
                if "INT" in t or "BIGINT" in t or "INTEGER" in t or "LONG" in t:
                    return "INT"
                if "FLOAT" in t or "DOUBLE" in t or "DECIMAL" in t or "NUMERIC" in t:
                    return "FLOAT"
                return t

            blame_dtype_mismatch = norm_type(actual_dtype) != norm_type(expected_dtype)
        except Exception:
            pass

    # 4. Probing join fanout risk (broken_join_fanout)
    blame_join_fanout_risk = False
    parent_pk_dupes = 0
    if parent_table and parent_pk_column:
        try:
            dupes = probe_dupe_count(con, parent_table, (parent_pk_column,))
            parent_pk_dupes = sum(max(0, d.count - 1) for d in dupes)
        except Exception:
            pass
    if blame_pk_dupe_count > 0 or parent_pk_dupes > 0:
        blame_join_fanout_risk = True

    # 5. Probing drift (unit_encoding_drift)
    blame_value_drift_z = 0.0
    if (
        blame_column
        and expected_mean is not None
        and expected_stddev is not None
        and expected_stddev > 0
    ):
        try:
            mean, std = probe_numeric_moments(con, blame_model, blame_column)
            blame_value_drift_z = abs(mean - expected_mean) / expected_stddev
        except Exception:
            pass

    # 6. Probing stale dimension (stale_dimension)
    blame_stale_dimension = False
    if parent_table:
        try:
            parts = parent_table.split(".")
            if len(parts) == 1:
                schema_clause = ""
                params = [parts[0]]
            elif len(parts) == 2:
                schema_clause = "table_schema = ? AND "
                params = [parts[0], parts[1]]
            else:
                schema_clause = "table_schema = ? AND "
                params = [parts[1], parts[2]]

            sql = (
                "SELECT column_name FROM information_schema.columns "
                f"WHERE {schema_clause}table_name = ? AND ("
                "  data_type LIKE '%TIMESTAMP%' OR data_type LIKE '%DATE%' OR data_type LIKE '%TIME%'"
                ")"
            )
            rows = con.execute(sql, params).fetchall()
            parent_lag_min = None
            if rows:
                ts_col = rows[0][0]
                fresh = probe_freshness(con, parent_table, ts_col, datetime.now(UTC))
                if fresh.lag_seconds is not None:
                    parent_lag_min = fresh.lag_seconds / 60.0

            blame_lag_min = blame_lag_ratio * 1440.0
            if parent_lag_min is not None and parent_lag_min > 1440.0 and blame_lag_min < 120.0:
                blame_stale_dimension = True
        except Exception:
            pass

    # 7. Probing skew/anomaly score (upstream_value_skew)
    blame_anomaly_score = 0.0
    if blame_column:
        try:
            skew_val = probe_skew(con, blame_model, blame_column)
            blame_anomaly_score = abs(skew_val)
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
        blame_lag_ratio=blame_lag_ratio,
        blame_integer_compatible_ratio=blame_integer_compatible_ratio,
        blame_float_compatible_ratio=blame_float_compatible_ratio,
        blame_dtype_mismatch=blame_dtype_mismatch,
        blame_join_fanout_risk=blame_join_fanout_risk,
        blame_value_drift_z=blame_value_drift_z,
        blame_stale_dimension=blame_stale_dimension,
        blame_anomaly_score=blame_anomaly_score,
        parent_table=parent_table,
        parent_pk_column=parent_pk_column,
        parent_row_count=parent_row_count,
        orphan_fk_count=orphan_fk_count,
    )
