"""TPC-H dataset config."""

from __future__ import annotations

from pathlib import Path

from eval.datasets.jaffle_shop import DatasetConfig, FaultTarget

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

TPCH = DatasetConfig(
    name="tpch",
    dbt_project_dir=REPO_ROOT / "pipelines" / "tpch_dbt",
    duckdb_path=REPO_ROOT / "pipelines" / "tpch_dbt" / "warehouse.duckdb",
    null_spike_target=FaultTarget(
        raw_table="raw_lineitem",
        column="l_partkey",
        pk_column="l_orderkey",
        expected_failing_test="not_null_stg_lineitem_l_partkey",
        expected_failing_model="stg_lineitem",
        expected_failing_column="l_partkey",
    ),
    duplicate_ingestion_target=FaultTarget(
        raw_table="raw_orders",
        column="o_orderkey",
        pk_column="o_orderkey",
        expected_failing_test="unique_stg_orders_o_orderkey",
        expected_failing_model="stg_orders",
        expected_failing_column="o_orderkey",
    ),
    broken_join_dropout_target=FaultTarget(
        raw_table="raw_supplier",
        column="s_suppkey",
        pk_column="s_suppkey",
        expected_failing_test="relationships_stg_lineitem_l_suppkey",
        expected_failing_model="stg_lineitem",
        expected_failing_column="l_suppkey",
    ),
)
