"""Sanity checks for the Pydantic schemas. Frozen → mutation should raise."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from dq_triage.models import (
    BlameLocation,
    GroundTruth,
    LineageEdge,
    RootCauseClass,
)


def test_blame_location_frozen() -> None:
    bl = BlameLocation(model="m", column="c", certainty=0.9)
    with pytest.raises(ValidationError):
        bl.certainty = 0.5  # type: ignore[misc]


def test_certainty_bounded() -> None:
    with pytest.raises(ValidationError):
        BlameLocation(model="m", column="c", certainty=1.1)


def test_lineage_edge_transform_type_validated() -> None:
    with pytest.raises(ValidationError):
        LineageEdge(
            downstream_model="d",
            downstream_column="dc",
            upstream_model="u",
            upstream_column="uc",
            transform_type="NOT_A_TYPE",  # type: ignore[arg-type]
            attribution_certainty=0.9,
        )


def test_ground_truth_class_enum() -> None:
    gt = GroundTruth(
        incident_key="k",
        cause_class=RootCauseClass.UPSTREAM_NULL_SPIKE,
        source_table="raw.orders",
        source_column="cust_id",
        offending_row_pks=("1", "2"),
        injected_at=datetime(2026, 5, 21, tzinfo=timezone.utc),
        fault_pattern="null_spike.flat_5pct",
    )
    assert gt.cause_class is RootCauseClass.UPSTREAM_NULL_SPIKE
