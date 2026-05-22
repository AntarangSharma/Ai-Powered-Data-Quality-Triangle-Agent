"""Ground-truth store: writes one JSON-lines file per benchmark suite.

We keep it as JSONL (not Postgres) because:
  - Reproducible: lives in git.
  - Inspectable: `cat eval/runs/<suite>/ground_truth.jsonl | jq`.
  - HuggingFace-friendly: directly uploadable as a dataset shard.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from dq_triage.models import GroundTruth


def write_jsonl(path: Path, truths: Iterable[GroundTruth]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for gt in truths:
            fh.write(gt.model_dump_json())
            fh.write("\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[GroundTruth]:
    out: list[GroundTruth] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(GroundTruth.model_validate(json.loads(line)))
    return out
