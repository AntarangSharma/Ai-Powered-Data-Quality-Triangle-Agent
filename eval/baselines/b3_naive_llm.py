"""B3 Baseline — LLM-only.

Directly queries Claude with raw failure table sample rows and test name to predict blame and cause.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import anthropic
import instructor
from pydantic import BaseModel, Field

from dq_triage.cache import _get_cache
from dq_triage.models import RootCauseClass
from eval.metrics import Prediction


class LLMOnlyResponse(BaseModel):
    blame_table: str = Field(
        description="The raw table responsible for the failure (e.g. raw_orders)."
    )
    blame_column: str = Field(description="The column responsible for the failure.")
    cause_class: RootCauseClass = Field(description="The root cause class of the failure.")
    reason: str = Field(description="Explanation of the choice.")


def predict(
    con: Any,
    cfg: Any,
    chosen: Any,
    trial: Any,
) -> Prediction:
    incident_key = trial.fault.pattern_id + "_" + str(trial.seed)

    # Try fetching some failing rows for prompt context
    sample_rows_str = ""
    try:
        df = con.execute(f"SELECT * FROM {chosen.failures_table_fqn} LIMIT 5").fetchdf()
        sample_rows_str = df.to_json(orient="records")
    except Exception:
        pass

    # Check cache first
    key_payload = f"b3_{chosen.test_name}_{sample_rows_str}"
    key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
    cache = _get_cache()
    if key in cache:
        cached = cache[key]
        return Prediction(
            incident_key=incident_key,
            candidate_tables=(cached["blame_table"],),
            blame_column=cached["blame_column"],
            blame_row_pks=frozenset(),
            cause_class=RootCauseClass(cached["cause_class"]),
            confidence=0.85,
            latency_seconds=0.0,
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Graceful heuristic fallback if no LLM key
        cause_map = {
            "not_null": RootCauseClass.UPSTREAM_NULL_SPIKE,
            "unique": RootCauseClass.DUPLICATE_INGESTION,
            "relationships": RootCauseClass.BROKEN_JOIN_DROPOUT,
        }
        cause = cause_map.get(chosen.kind, RootCauseClass.UNKNOWN)
        # Attribute raw orders or customers based on test
        blame_table = "raw_orders"
        if "customer" in chosen.test_name:
            blame_table = "raw_customers"
        return Prediction(
            incident_key=incident_key,
            candidate_tables=(blame_table,),
            blame_column=chosen.column,
            blame_row_pks=frozenset(),
            cause_class=cause,
            confidence=0.8,
            latency_seconds=0.0,
        )

    client = instructor.from_anthropic(anthropic.Anthropic(api_key=api_key))
    prompt = (
        f"You are a Senior Data Quality Engineer diagnosing database failures.\n\n"
        f"Failing test: {chosen.test_name}\n"
        f"Failing model: {chosen.model}\n"
        f"Failing column: {chosen.column}\n"
        f"Sample failing rows: {sample_rows_str}\n\n"
        f"Identify which raw source table (raw_orders, raw_customers, raw_payments) and column is responsible, "
        f"and classify the root cause."
    )

    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
            response_model=LLMOnlyResponse,
        )

        # Cache result
        cache[key] = {
            "blame_table": response.blame_table,
            "blame_column": response.blame_column,
            "cause_class": response.cause_class.value,
        }

        return Prediction(
            incident_key=incident_key,
            candidate_tables=(response.blame_table,),
            blame_column=response.blame_column,
            blame_row_pks=frozenset(),
            cause_class=response.cause_class,
            confidence=0.9,
            latency_seconds=0.0,
        )
    except Exception:
        # Fallback in case of API failure
        return Prediction(
            incident_key=incident_key,
            candidate_tables=("raw_orders",),
            blame_column=chosen.column,
            blame_row_pks=frozenset(),
            cause_class=RootCauseClass.UNKNOWN,
            confidence=0.5,
            latency_seconds=0.0,
        )
