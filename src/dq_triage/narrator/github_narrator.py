"""GitHub PR alert narrator and poster."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

import structlog

from dq_triage.narrator.composer import compose

if TYPE_CHECKING:
    from dq_triage.models import Incident

logger = structlog.get_logger()


def post_pr_comment(incident: Incident, pr_number: int | None = None) -> bool:
    """Compose and post a premium markdown comment onto a GitHub Pull Request.

    Resolves PR number via env or parameter. Uses standard library urllib.request
    to keep dependencies minimal, fast, and robust.
    """
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    # Resolve PR number
    if pr_number is None:
        event_path = os.environ.get("GITHUB_EVENT_PATH")
        if event_path and os.path.exists(event_path):
            try:
                with open(event_path, encoding="utf-8") as f:
                    event = json.load(f)
                pr_number = event.get("number") or event.get("pull_request", {}).get("number")
            except Exception as e:
                logger.warning("Failed to parse GITHUB_EVENT_PATH", error=str(e))

        if pr_number is None:
            pr_env = os.environ.get("GITHUB_PR_NUMBER")
            if pr_env:
                try:
                    pr_number = int(pr_env)
                except ValueError:
                    pass

    # Build narrated content
    try:
        narrated = compose(incident)
    except Exception as e:
        logger.error("Failed to compose narrative for GitHub comment", error=str(e))
        return False

    # Construct premium comment body
    body = (
        f"## 🚨 Data Quality Failure Diagnosed: {narrated.headline}\n\n"
        f"| Attribute | Details |\n"
        f"|---|---|\n"
        f"| **Incident ID** | `{incident.incident_id}` |\n"
        f"| **Failing Test** | `{incident.failing_test_name}` |\n"
        f"| **Failing Model** | `{incident.failing_model}.{incident.failing_column or '*'}` |\n"
        f"| **Attributed Blame** | `{incident.blame_location.model}.{incident.blame_location.column or '*'}` |\n\n"
        f"---\n\n"
        f"### 🔍 Root-Cause Analysis\n\n"
        f"{narrated.narrative}\n\n"
        f"### 💡 Suggested Actionable Fix\n\n"
        f"```sql\n"
        f"{narrated.one_line_fix}\n"
        f"```\n"
    )

    if not token or not repo or not pr_number:
        logger.info(
            "GitHub env parameters missing — printing PR comment payload",
            repo=repo,
            pr_number=pr_number,
            comment_body=body,
        )
        return False

    # POST to GitHub Pull Request API
    # Endpoints shape: /repos/{owner}/{repo}/issues/{issue_number}/comments
    url = f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments"
    data = json.dumps({"body": body}).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "dq-triage-agent",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            logger.info("Successfully posted GitHub PR comment", comment_id=res_json.get("id"))
            return True
    except urllib.error.HTTPError as e:
        logger.error(
            "GitHub API HTTPError posting comment",
            status=e.code,
            reason=e.reason,
            url=url,
        )
        return False
    except Exception as e:
        logger.error("Failed to post GitHub PR comment", error=str(e))
        return False
