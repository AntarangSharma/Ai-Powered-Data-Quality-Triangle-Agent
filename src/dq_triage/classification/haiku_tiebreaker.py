"""Anthropic Haiku LLM tiebreaker.

Fires only when rule-based confidence is low or close. Refines ranking using Claude Haiku.
"""

from __future__ import annotations

import hashlib
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
from dq_triage.models import ClassScore, RootCauseClass

if TYPE_CHECKING:
    from dq_triage.agent.evidence import ClassifierEvidence


class TiebreakerRanking(BaseModel):
    cause_class: RootCauseClass
    score: float = Field(ge=0.0, le=1.0, description="Confidence score from 0.0 to 1.0.")
    reason: str = Field(description="Brief explanation of why this class was chosen or ranked.")


class HaikuTiebreakerResponse(BaseModel):
    ranked_classes: list[TiebreakerRanking] = Field(
        description="The refined list of ranked classes, sorted by score descending."
    )


# Rates per token for Claude 3 Haiku (claude-3-haiku-20240307)
# Input: $0.25 / M tokens = $0.00000025 per token
# Output: $1.25 / M tokens = $0.00000125 per token
HAIKU_INPUT_RATE = 0.25 / 1_000_000
HAIKU_OUTPUT_RATE = 1.25 / 1_000_000


def haiku_tiebreaker(
    evidence: ClassifierEvidence,
    ranked: tuple[ClassScore, ...],
) -> tuple[ClassScore, ...]:
    """Refine class rankings using Claude Haiku when rules are uncertain."""
    # 1. Check cache first
    top_3_names = ",".join(c.cause_class.value for c in ranked[:3])
    key_payload = evidence.canonical_json() + top_3_names
    key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()

    cache = _get_cache()
    if key in cache:
        cached_val = cache[key]
        # Ensure we return a tuple of ClassScore objects
        return tuple(
            ClassScore(
                cause_class=RootCauseClass(item["cause_class"]),
                score=item["score"],
                evidence_keys=tuple(item["evidence_keys"]),
            )
            for item in cached_val
        )

    # 2. Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Fall back gracefully to rules if no key is present in tests/dry-run
        return ranked

    # 3. Check budget cap
    max_budget = _get_max_budget()
    spent = read_budget_spent()
    if spent >= max_budget:
        raise BudgetExceededError(
            f"LLM budget limit of ${max_budget:.4f} exceeded (spent: ${spent:.4f})."
        )

    client = instructor.from_anthropic(anthropic.Anthropic(api_key=api_key))

    messages = [
        {
            "role": "user",
            "content": (
                f"You are a Senior Data Quality Engineer diagnosing database failures.\n\n"
                f"Statistical evidence collected:\n{evidence.canonical_json()}\n\n"
                f"Current candidate rankings from rules:\n"
                + "\n".join(f"- {c.cause_class.value}: {c.score:.2f}" for c in ranked)
                + "\n\nAnalyze the evidence and rules, then refine the class scores and output the new ranking."
            ),
        }
    ]

    response, completion = client.messages.create_with_completion(
        model="claude-3-haiku-20240307",
        max_tokens=1000,
        messages=messages,  # type: ignore[arg-type]
        response_model=HaikuTiebreakerResponse,
    )

    # 4. Calculate cost and update budget
    input_tokens = completion.usage.input_tokens
    output_tokens = completion.usage.output_tokens
    cost = (input_tokens * HAIKU_INPUT_RATE) + (output_tokens * HAIKU_OUTPUT_RATE)

    new_spent = spent + cost
    _write_budget_spent(new_spent)

    if new_spent > max_budget:
        # Roll back write if we strictly want to refuse, but since we already spent it, we record it and raise.
        raise BudgetExceededError(
            f"LLM budget limit of ${max_budget:.4f} exceeded (spent: ${new_spent:.4f})."
        )

    # 5. Format results and cache them
    refined_ranked = []
    for item in response.ranked_classes:
        refined_ranked.append(
            ClassScore(
                cause_class=item.cause_class,
                score=item.score,
                evidence_keys=("llm_tiebreaker",),
            )
        )

    if not refined_ranked:
        return ranked

    # Cache format-friendly representation
    cache_val = [
        {
            "cause_class": c.cause_class.value,
            "score": c.score,
            "evidence_keys": list(c.evidence_keys),
        }
        for c in refined_ranked
    ]
    cache[key] = cache_val

    return tuple(refined_ranked)
