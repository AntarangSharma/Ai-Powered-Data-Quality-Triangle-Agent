"""Self-healing code generator to automatically propose dbt fixes for incidents."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import anthropic
import instructor
import structlog
from pydantic import BaseModel, Field

from dq_triage.attribution.manifest import Manifest
from dq_triage.cache import (
    BudgetExceededError,
    _get_cache,
    _get_max_budget,
    _write_budget_spent,
    read_budget_spent,
)

if TYPE_CHECKING:
    from dq_triage.models import Incident

logger = structlog.get_logger()


class CodeFixProposal(BaseModel):
    model_config = {"frozen": True}

    file_path: str = Field(
        description="Relative path to the dbt model SQL file from the project root."
    )
    original_code: str = Field(description="The original SQL code of the model.")
    corrected_code: str = Field(description="The corrected SQL code with the fix implemented.")
    explanation: str = Field(description="Brief explanation of why this fix resolves the incident.")
    branch_name: str = Field(
        description="A clean, git-safe branch name, e.g., 'fix/null-spike-raw-orders-user-id'."
    )


# Claude 3.5 Sonnet (claude-3-5-sonnet-20240620) pricing per token:
# Input: $3.00 / M tokens = $0.000003 per token
# Output: $15.00 / M tokens = $0.000015 per token
SONNET_INPUT_RATE = 3.00 / 1_000_000
SONNET_OUTPUT_RATE = 15.00 / 1_000_000


def propose_fix(incident: Incident, dbt_project_dir: Path) -> CodeFixProposal:
    """Proposes a code fix for a diagnosed Incident by modifying the blamed dbt model source code."""
    manifest = Manifest(dbt_project_dir)
    blame_model = incident.blame_location.model

    node = manifest.by_name.get(blame_model)
    if not node:
        raise LookupError(f"Blame model '{blame_model}' not found in dbt manifest.")

    if node.kind != "model":
        raise ValueError(
            f"Blame target '{blame_model}' is of kind '{node.kind}' (not 'model'). Cannot heal non-models."
        )

    sql_file = dbt_project_dir / node.original_file_path
    if not sql_file.exists():
        raise FileNotFoundError(f"Source file for model '{blame_model}' not found at '{sql_file}'.")

    source_code = sql_file.read_text(encoding="utf-8")

    # 1. Check cache first
    cache = _get_cache()
    cache_key = f"self_healing|{incident.incident_id}|{hash(source_code)}"
    if cache_key in cache:
        cached_val = cache[cache_key]
        return CodeFixProposal(**cached_val)

    # 2. Check API key
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        # Fallback for dry runs and tests
        return CodeFixProposal(
            file_path=node.original_file_path,
            original_code=source_code,
            corrected_code=source_code
            + f"\n-- FIX: Filter out invalid / null values for incident {incident.incident_id}\n",
            explanation="Completed in fallback mode because no Anthropic API key was configured. Added filter comment.",
            branch_name=f"fix/dq-{incident.incident_id}",
        )

    # 3. Check budget
    max_budget = _get_max_budget()
    spent = read_budget_spent()
    if spent >= max_budget:
        raise BudgetExceededError(
            f"LLM budget limit of ${max_budget:.4f} exceeded (spent: ${spent:.4f})."
        )

    # 4. Call Claude Sonnet using Instructor
    client = instructor.from_anthropic(anthropic.Anthropic(api_key=api_key))

    incident_json = incident.model_dump_json()

    prompt = (
        f"You are a Senior Data Quality and Analytics Engineer.\n"
        f"We have detected a Data Quality Failure in our dbt pipeline and attributed the root cause using our triage agent.\n\n"
        f"Incident Diagnosis:\n{incident_json}\n\n"
        f"Target File to Fix: {node.original_file_path}\n"
        f"Current Source Code:\n```sql\n{source_code}\n```\n\n"
        f"Please analyze the incident and propose a precise SQL change to fix the issue in the target model. "
        f"For upstream null spikes, you could add COALESCE or filtering. For duplicate ingestion, you might use a row_number() window or qualify filter. "
        f"For join dropouts, add fallback values or safe joins. Make sure to match the existing SQL style and formatting."
    )

    try:
        response, completion = client.messages.create_with_completion(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
            response_model=CodeFixProposal,
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

    except Exception as e:
        logger.error("Failed to call LLM for self-healing fix", error=str(e))
        raise
