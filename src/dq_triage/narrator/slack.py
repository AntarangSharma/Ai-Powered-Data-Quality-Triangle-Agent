"""Slack alert narrator and poster."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog
from slack_sdk import WebClient

from dq_triage.models import Verdict

if TYPE_CHECKING:
    from dq_triage.models import Incident
    from dq_triage.narrator.composer import NarratedIncident

logger = structlog.get_logger()


def post(narrated: NarratedIncident, incident: Incident, channel: str) -> str:
    """Post a rich, interactive Block-Kit notification to Slack.

    Returns the thread timestamp `ts` for downstream threading / updates.
    """
    # Color-coded emoji indicator based on Verdict
    emoji = {
        Verdict.AUTO: "🟢 [AUTO-RESOLVED]",
        Verdict.TWO_CANDIDATE: "🟡 [TWO-CANDIDATE]",
        Verdict.TRIAGE_ONLY: "🔴 [TRIAGE-ONLY]",
    }.get(incident.verdict_type, "⏸")

    # Construct premium Block-Kit payload
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{emoji} {narrated.headline}",
                "emoji": True,
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"*Incident:* `{incident.incident_id}` | "
                        f"*Failing Test:* `{incident.failing_test_name}` | "
                        f"*Failing Model:* `{incident.failing_model}.{incident.failing_column or '*'}`"
                    ),
                }
            ],
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": narrated.narrative,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Suggested One-Line Fix:*\n```{narrated.one_line_fix}```",
            },
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "👍 Accept Verdict",
                        "emoji": True,
                    },
                    "style": "primary",
                    "value": f"feedback|{incident.incident_id}|accept",
                    "action_id": "accept_verdict",
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "👎 Reject Verdict",
                        "emoji": True,
                    },
                    "style": "danger",
                    "value": f"feedback|{incident.incident_id}|reject",
                    "action_id": "reject_verdict",
                },
            ],
        },
    ]

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        # Fall back to structured logging when token is missing (tests / local dev)
        logger.info(
            "Slack token missing — printing Block-Kit payload",
            channel=channel,
            blocks=blocks,
        )
        return "1234567890.123456"

    client = WebClient(token=token)
    response = client.chat_postMessage(
        channel=channel,
        blocks=blocks,
        text=f"{emoji} Data quality triage alert for {incident.failing_model}",
    )
    return str(response["ts"])
