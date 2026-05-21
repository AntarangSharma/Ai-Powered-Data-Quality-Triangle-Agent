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
    # Per-fault-class targets. Each fault class has different mechanics, so
    # giving each its own target keeps the fault implementations dataset-agnostic.
    null_spike_target: FaultTarget
    duplicate_ingestion_target: FaultTarget
    broken_join_dropout_target: FaultTarget


REPO_ROOT = Path(__file__).resolve().parent.parent.parent

JAFFLE_SHOP = DatasetConfig(
    name="jaffle_shop",
    dbt_project_dir=REPO_ROOT / "pipelines" / "jaffle_shop",
    duckdb_path=REPO_ROOT / "pipelines" / "jaffle_shop" / "warehouse.duckdb",
    # NULL-spike: write NULLs into raw_orders.user_id → breaks
    # not_null_stg_orders_customer_id (W1 target — kept stable for diffing).
    null_spike_target=FaultTarget(
        raw_table="raw_orders",
        column="user_id",
        pk_column="id",
        expected_failing_test="not_null_stg_orders_customer_id",
        expected_failing_model="stg_orders",
        expected_failing_column="customer_id",
    ),
    # Duplicate ingestion: re-insert rows with existing raw_orders.id values
    # → breaks unique_stg_orders_order_id. The attributor should still land
    # on raw_orders.id; classification of UPSTREAM_NULL_SPIKE vs
    # DUPLICATE_INGESTION is the Week-3 classifier's job.
    duplicate_ingestion_target=FaultTarget(
        raw_table="raw_orders",
        column="id",
        pk_column="id",
        expected_failing_test="unique_stg_orders_order_id",
        expected_failing_model="stg_orders",
        expected_failing_column="order_id",
    ),
    # Broken-join dropout: delete rows from raw_customers whose id is
    # referenced by raw_orders.user_id → breaks
    # relationships_stg_orders_customer_id_*. The attributor lands on
    # raw_orders.user_id (where the orphan FKs live); the deletion event in
    # raw_customers is captured in GroundTruth.notes for classifier eval.
    broken_join_dropout_target=FaultTarget(
        raw_table="raw_customers",
        column="id",
        pk_column="id",
        # NB: dbt hashes long relationships test names — match prefix only.
        expected_failing_test="relationships_stg_orders_customer_id",
        expected_failing_model="stg_orders",
        expected_failing_column="customer_id",
    ),
)
