"""Jaffle Shop dataset config."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FaultTarget:
    """Where a fault is applied + what test it's expected to break."""

    raw_table: str  # in DuckDB
    column: str
    pk_column: str
    # Which downstream dbt test we expect to break (for ground-truth pairing).
    expected_failing_test: str  # e.g. "not_null_stg_orders_customer_id"
    expected_failing_model: str  # e.g. "stg_orders"
    expected_failing_column: str  # e.g. "customer_id"


@dataclass(frozen=True, slots=True)
class DatasetConfig:
    name: str
    dbt_project_dir: Path
    duckdb_path: Path
    null_spike_target: FaultTarget


REPO_ROOT = Path(__file__).resolve().parent.parent.parent

JAFFLE_SHOP = DatasetConfig(
    name="jaffle_shop",
    dbt_project_dir=REPO_ROOT / "pipelines" / "jaffle_shop",
    duckdb_path=REPO_ROOT / "pipelines" / "jaffle_shop" / "warehouse.duckdb",
    null_spike_target=FaultTarget(
        raw_table="raw_orders",
        column="user_id",
        pk_column="id",
        expected_failing_test="not_null_stg_orders_customer_id",
        expected_failing_model="stg_orders",
        expected_failing_column="customer_id",
    ),
)
