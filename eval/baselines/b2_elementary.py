"""B2 Baseline — random-blame.

Selects a random source table as blame and a random cause class.
"""

from __future__ import annotations

import random
from typing import Any

from dq_triage.models import RootCauseClass
from eval.metrics import Prediction


def predict(
    con: Any,
    cfg: Any,
    chosen: Any,
    trial: Any,
) -> Prediction:
    # Source tables in jaffle_shop (or generic fallback)
    raw_tables = ["raw_customers", "raw_orders", "raw_payments"]
    blame_table = random.choice(raw_tables)

    causes = [
        RootCauseClass.UPSTREAM_NULL_SPIKE,
        RootCauseClass.DUPLICATE_INGESTION,
        RootCauseClass.BROKEN_JOIN_DROPOUT,
    ]
    cause = random.choice(causes)

    return Prediction(
        incident_key=trial.fault.pattern_id + "_" + str(trial.seed),
        candidate_tables=(blame_table,),
        blame_column=chosen.column,
        blame_row_pks=frozenset(),
        cause_class=cause,
        confidence=0.33,
        latency_seconds=0.0,
    )
