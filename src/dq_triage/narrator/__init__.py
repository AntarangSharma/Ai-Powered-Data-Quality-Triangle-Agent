"""Narrator module.

Turns triaged incidents into human-readable alerts and posts them to Slack or GitHub.
"""

from dq_triage.narrator.composer import NarratedIncident, compose
from dq_triage.narrator.github_narrator import post_pr_comment
from dq_triage.narrator.slack import post

__all__ = ["NarratedIncident", "compose", "post", "post_pr_comment"]
