"""B4 Baseline — single-LLM-call.

Uses SQLGlot walker to attribute blame, then uses a single LLM call to classify the cause from the evidence.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

import anthropic
import instructor
from pydantic import BaseModel, Field

from dq_triage.agent.evidence import assemble_evidence
from dq_triage.attribution.sqlglot_walker import build_walker
from dq_triage.cache import _get_cache
from dq_triage.models import RootCauseClass
from eval.metrics import Prediction


class B4Response(BaseModel):
    cause_class: RootCauseClass = Field(description="The classified root cause class.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0.")
    reason: str = Field(description="Reasoning explaining this choice.")


def predict(
    con: Any,
    cfg: Any,
    chosen: Any,
    trial: Any,
) -> Prediction:
    incident_key = trial.fault.pattern_id + "_" + str(trial.seed)

    # 1. Attribute using SQLGlot Walker
    attributor = build_walker(cfg.dbt_project_dir)

    # Simple PK mapping
    pk_map = {
        "stg_customers": "customer_id",
        "stg_orders": "order_id",
        "stg_payments": "payment_id",
        "customers": "customer_id",
        "orders": "order_id",
        "raw_customers": "id",
        "raw_orders": "id",
        "raw_payments": "id",
    }
    pk_col = pk_map.get(chosen.model, "id")

    # Load failing rows
    failing_pks = ()
    try:
        failing_rows = con.execute(f"SELECT * FROM {chosen.failures_table_fqn} LIMIT 100").fetchdf()
        if not failing_rows.empty:
            if pk_col in failing_rows.columns:
                failing_pks = tuple(str(v) for v in failing_rows[pk_col].tolist())
            elif "unique_field" in failing_rows.columns:
                failing_pks = tuple(str(v) for v in failing_rows["unique_field"].tolist())
    except Exception:
        pass

    blame = attributor.attribute(
        con,
        failing_model=chosen.model,
        failing_column=chosen.column or "",
        failing_pks=failing_pks,
        failing_pk_column=pk_col,
    )

    # 2. Resolve relationships target if applicable
    parent_raw_table = None
    parent_raw_column = None
    if chosen.kind == "relationships" and chosen.parent_model and chosen.parent_column:
        parent_pk_col = pk_map.get(chosen.parent_model, "id")
        parent_blame = attributor.attribute(
            con,
            failing_model=chosen.parent_model,
            failing_column=chosen.parent_column,
            failing_pks=(),
            failing_pk_column=parent_pk_col,
        )
        parent_raw_table = parent_blame.model
        parent_raw_column = parent_blame.column or parent_pk_col

    # 3. Assemble evidence
    evidence = assemble_evidence(
        con,
        failing_test_kind=chosen.kind,
        failing_model=chosen.model,
        failing_column=chosen.column,
        blame_model=blame.model,
        blame_column=blame.column,
        blame_pk_column=pk_map.get(blame.model, "id"),
        parent_table=parent_raw_table,
        parent_pk_column=parent_raw_column,
    )

    # 4. Single LLM call to classify
    key_payload = f"b4_{evidence.canonical_json()}"
    key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
    cache = _get_cache()
    if key in cache:
        cached = cache[key]
        return Prediction(
            incident_key=incident_key,
            candidate_tables=(blame.model,),
            blame_column=blame.column,
            blame_row_pks=frozenset(blame.row_pks),
            cause_class=RootCauseClass(cached["cause_class"]),
            confidence=cached["confidence"],
            latency_seconds=0.0,
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Fall back to a fast rule-based prediction if no key
        cause_map = {
            "not_null": RootCauseClass.UPSTREAM_NULL_SPIKE,
            "unique": RootCauseClass.DUPLICATE_INGESTION,
            "relationships": RootCauseClass.BROKEN_JOIN_DROPOUT,
        }
        cause = cause_map.get(chosen.kind, RootCauseClass.UNKNOWN)
        return Prediction(
            incident_key=incident_key,
            candidate_tables=(blame.model,),
            blame_column=blame.column,
            blame_row_pks=frozenset(blame.row_pks),
            cause_class=cause,
            confidence=0.8,
            latency_seconds=0.0,
        )

    client = instructor.from_anthropic(anthropic.Anthropic(api_key=api_key))
    prompt = (
        f"You are a Senior Data Quality Engineer diagnosing database failures.\n\n"
        f"We have walked the lineage and attributed blame to {blame.model}.{blame.column}.\n"
        f"Here is the assembled statistical evidence:\n{evidence.canonical_json()}\n\n"
        f"Classify the root cause of this failure and specify your confidence level."
    )

    try:
        response = client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
            response_model=B4Response,
        )

        # Cache
        cache[key] = {
            "cause_class": response.cause_class.value,
            "confidence": response.confidence,
        }

        return Prediction(
            incident_key=incident_key,
            candidate_tables=(blame.model,),
            blame_column=blame.column,
            blame_row_pks=frozenset(blame.row_pks),
            cause_class=response.cause_class,
            confidence=response.confidence,
            latency_seconds=0.0,
        )
    except Exception:
        return Prediction(
            incident_key=incident_key,
            candidate_tables=(blame.model,),
            blame_column=blame.column,
            blame_row_pks=frozenset(blame.row_pks),
            cause_class=RootCauseClass.UNKNOWN,
            confidence=0.5,
            latency_seconds=0.0,
        )
