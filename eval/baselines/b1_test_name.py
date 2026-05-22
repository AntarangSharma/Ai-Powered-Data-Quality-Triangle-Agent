"""B1 Baseline — dbt-test-only.

Predicts blame model/column as the failing test model/column directly.
Maps test kind directly to root cause class.
"""

from __future__ import annotations

from typing import Any

from dq_triage.models import RootCauseClass
from eval.metrics import Prediction


def predict(
    con: Any,
    cfg: Any,
    chosen: Any,
    trial: Any,
) -> Prediction:
    # Maps test kind to likely root cause
    cause_map = {
        "not_null": RootCauseClass.UPSTREAM_NULL_SPIKE,
        "unique": RootCauseClass.DUPLICATE_INGESTION,
        "relationships": RootCauseClass.BROKEN_JOIN_DROPOUT,
    }
    cause = cause_map.get(chosen.kind, RootCauseClass.UNKNOWN)

    return Prediction(
        incident_key=trial.fault.pattern_id + "_" + str(trial.seed),
        candidate_tables=(chosen.model,),
        blame_column=chosen.column,
        blame_row_pks=frozenset(),
        cause_class=cause,
        confidence=1.0,
        latency_seconds=0.0,
    )
