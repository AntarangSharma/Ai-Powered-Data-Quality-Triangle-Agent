"""Pydantic data model — the source of truth for every piece of state in the system.

Design rules:
- Every model is `frozen=True` (immutable). Mutation = new object.
- No `Any` in public fields except `dict[str, Any]` for row samples (warehouse-typed).
- All scoring fields are `float` constrained to [0, 1].
- Enums for closed sets — never raw strings on the wire.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RootCauseClass(StrEnum):
    LATE_ARRIVING = "late_arriving"
    UPSTREAM_NULL_SPIKE = "upstream_null_spike"
    TYPE_COERCION = "type_coercion"
    DUPLICATE_INGESTION = "duplicate_ingestion"
    BROKEN_JOIN_FANOUT = "broken_join_fanout"
    BROKEN_JOIN_DROPOUT = "broken_join_dropout"
    SOURCE_SCHEMA_CHANGE = "source_schema_change"
    STALE_DIMENSION = "stale_dimension"
    UNIT_ENCODING_DRIFT = "unit_encoding_drift"
    UNKNOWN = "unknown"


TransformType = Literal["DIRECT", "CAST", "AGG", "JOIN_KEY", "CASE", "UDF", "WINDOW", "OPAQUE"]


class Verdict(StrEnum):
    """How the agent chose to report the incident."""

    AUTO = "auto"  # confidence ≥ 0.85, single top class
    TWO_CANDIDATE = "two_candidate"  # 0.6-0.85 or top-2 close
    TRIAGE_ONLY = "triage_only"  # < 0.6, surface evidence, no verdict


# ---------------------------------------------------------------------------
# Frozen base
# ---------------------------------------------------------------------------


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ---------------------------------------------------------------------------
# Attribution (the "where" stage)
# ---------------------------------------------------------------------------


class LineageEdge(Frozen):
    downstream_model: str
    downstream_column: str
    upstream_model: str
    upstream_column: str
    transform_type: TransformType
    attribution_certainty: float = Field(ge=0.0, le=1.0)
    source: Literal["sqlglot", "llm_fallback"] = "sqlglot"


class BlameLocation(Frozen):
    """The Attributor's answer to 'where did the bad data come from?'."""

    model: str
    column: str | None
    row_pks: tuple[str, ...] = Field(default_factory=tuple)
    certainty: float = Field(ge=0.0, le=1.0)
    walk_path: tuple[LineageEdge, ...] = Field(default_factory=tuple)
    hit_agg_boundary: bool = False


# ---------------------------------------------------------------------------
# Evidence (between stages)
# ---------------------------------------------------------------------------


class UpstreamStat(Frozen):
    model: str
    column: str
    null_rate_today: float = Field(ge=0.0, le=1.0)
    null_rate_p50_30d: float = Field(ge=0.0, le=1.0)
    null_rate_z: float
    distinct_count: int = Field(ge=0)
    dtype: str
    last_loaded_at: datetime
    rows_in_last_load: int = Field(ge=0)
    anomaly_score: float = Field(ge=0.0, le=1.0)


class RecentLoad(Frozen):
    model: str
    loaded_at: datetime
    row_count: int = Field(ge=0)


class CodeChange(Frozen):
    sha: str
    file: str
    message: str
    ts: datetime


class FreshnessInfo(Frozen):
    source_table: str
    lag_minutes: float = Field(ge=0.0)
    sla_minutes: float = Field(ge=0.0)

    @property
    def lag_ratio(self) -> float:
        return self.lag_minutes / self.sla_minutes if self.sla_minutes else 0.0


class EvidenceBundle(Frozen):
    """Token-budgeted payload assembled for the Classifier (and LLM if needed)."""

    failing_test_name: str
    failing_model: str
    failing_column: str | None
    failure_count: int = Field(ge=0)
    failing_rows_sample: tuple[dict[str, Any], ...] = Field(max_length=5)
    blame_location: BlameLocation
    upstream_stats: tuple[UpstreamStat, ...]
    recent_loads: tuple[RecentLoad, ...]
    recent_code_changes: tuple[CodeChange, ...] = Field(max_length=3)
    source_freshness: tuple[FreshnessInfo, ...] = Field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Classification (the "what" stage)
# ---------------------------------------------------------------------------


class ClassScore(Frozen):
    cause_class: RootCauseClass
    score: float = Field(ge=0.0, le=1.0)
    evidence_keys: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Names of evidence fields that contributed (for explainability).",
    )


class RootCauseHypothesis(Frozen):
    cause_class: RootCauseClass
    blame_model: str
    blame_column: str | None
    blame_rows_sample: tuple[dict[str, Any], ...] = Field(max_length=5)
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_calibrated: float = Field(ge=0.0, le=1.0)
    evidence_summary: str = Field(max_length=600)
    suggested_one_line_fix: str = Field(max_length=240)


# ---------------------------------------------------------------------------
# Incident — the top-level persisted record
# ---------------------------------------------------------------------------


class Incident(Frozen):
    incident_id: str
    created_at: datetime
    failing_test_name: str
    failing_model: str
    failing_column: str | None
    failing_row_count: int = Field(ge=0)
    failing_rows_sample: tuple[dict[str, Any], ...] = Field(max_length=5)
    blame_location: BlameLocation
    upstream_stats: tuple[UpstreamStat, ...]
    class_scores: tuple[ClassScore, ...]
    hypotheses: tuple[RootCauseHypothesis, ...] = Field(min_length=1, max_length=3)
    verdict_type: Verdict
    final_verdict: RootCauseHypothesis | None  # None ⇔ TRIAGE_ONLY
    human_label: RootCauseClass | None = None
    time_to_verdict_seconds: float = Field(ge=0.0)
    token_cost_usd: float = Field(ge=0.0)


# ---------------------------------------------------------------------------
# Ground truth (eval only — never seen by the agent)
# ---------------------------------------------------------------------------


class GroundTruth(Frozen):
    incident_key: str  # stable hash of (dataset, fault, seed)
    cause_class: RootCauseClass
    source_table: str
    source_column: str | None
    offending_row_pks: tuple[str, ...]
    injected_at: datetime
    fault_pattern: str  # e.g. "null_spike.flat_5pct"
    notes: str = ""
