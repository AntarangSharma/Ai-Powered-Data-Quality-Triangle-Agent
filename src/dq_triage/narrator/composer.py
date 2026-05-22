"""Composer for drafting human-readable incident narratives using Claude Sonnet."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import anthropic
import instructor
from pydantic import BaseModel, Field

from dq_triage.cache import (
    BudgetExceededError,
    _get_cache,
    _get_max_budget,
    _write_budget_spent,
    read_budget_spent,
)

if TYPE_CHECKING:
    from dq_triage.models import Incident


class NarratedIncident(BaseModel):
    model_config = {"frozen": True}

    headline: str = Field(description="Punchy, concise headline for the Slack alert.")
    narrative: str = Field(
        description="A 3-paragraph detailed narrative explaining: 1) What happened, 2) The blame location (where), and 3) Next steps/suggested fix."
    )
    one_line_fix: str = Field(
        description="A single actionable command or SQL snippet to fix the issue."
    )


# Claude 3.5 Sonnet (claude-3-5-sonnet-20240620) pricing per token:
# Input: $3.00 / M tokens = $0.000003 per token
# Output: $15.00 / M tokens = $0.000015 per token
SONNET_INPUT_RATE = 3.00 / 1_000_000
SONNET_OUTPUT_RATE = 15.00 / 1_000_000


def compose(incident: Incident) -> NarratedIncident:
    """Turn an Incident into a high-quality narrative using Claude Sonnet."""
    # 1. Check cache first
    cache = _get_cache()
    cache_key = f"narration|{incident.incident_id}"
    if cache_key in cache:
        cached_val = cache[cache_key]
        return NarratedIncident(**cached_val)

    # 2. Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Graceful fallback for local dry-runs / tests
        top_cause = (
            incident.final_verdict.cause_class.value
            if incident.final_verdict is not None
            else (
                incident.class_scores[0].cause_class.value if incident.class_scores else "unknown"
            )
        )
        one_fix = (
            incident.final_verdict.suggested_one_line_fix
            if incident.final_verdict is not None
            else "Inspect the raw warehouse table."
        )
        fallback = NarratedIncident(
            headline=f"🚨 Data Quality Failure: {incident.failing_test_name}",
            narrative=(
                f"A test failure was detected in model `{incident.failing_model}` on column `{incident.failing_column or '*'}`.\n\n"
                f"The attribution walker tracked the root blame to `{incident.blame_location.model}` column `{incident.blame_location.column or '*'}`. "
                f"Rules classification determined the likely cause is `{top_cause}` with the current evidence.\n\n"
                f"This notification was compiled in fallback mode because no Anthropic API key was configured."
            ),
            one_line_fix=one_fix,
        )
        return fallback

    # 3. Check budget limit
    max_budget = _get_max_budget()
    spent = read_budget_spent()
    if spent >= max_budget:
        raise BudgetExceededError(
            f"LLM budget limit of ${max_budget:.4f} exceeded (spent: ${spent:.4f})."
        )

    # 4. Call Claude Sonnet using Instructor
    client = instructor.from_anthropic(anthropic.Anthropic(api_key=api_key))

    # Convert incident to JSON for model context
    incident_json = incident.model_dump_json()

    messages = [
        {
            "role": "user",
            "content": (
                f"You are an expert AI Data Reliability Engineer.\n"
                f"You have been handed a diagnosed Data Quality Incident represented in JSON below.\n\n"
                f"Incident Data:\n{incident_json}\n\n"
                f"Please compile a professional and precise narrative report for the team, detailing what went wrong, "
                f"the root blame, and how to fix it."
            ),
        }
    ]

    response, completion = client.messages.create_with_completion(
        model="claude-3-5-sonnet-20240620",
        max_tokens=1500,
        messages=messages,  # type: ignore[arg-type]
        response_model=NarratedIncident,
    )

    # 5. Track tokens and cost
    input_tokens = completion.usage.input_tokens
    output_tokens = completion.usage.output_tokens
    cost = (input_tokens * SONNET_INPUT_RATE) + (output_tokens * SONNET_OUTPUT_RATE)

    new_spent = spent + cost
    _write_budget_spent(new_spent)

    if new_spent > max_budget:
        raise BudgetExceededError(
            f"LLM budget limit of ${max_budget:.4f} exceeded (spent: ${new_spent:.4f})."
        )

    # 6. Cache and return
    cache[cache_key] = response.model_dump()
    return response
