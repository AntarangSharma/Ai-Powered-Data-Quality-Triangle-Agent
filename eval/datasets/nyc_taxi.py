"""NYC Taxi dataset config."""

from __future__ import annotations

from pathlib import Path

from eval.datasets.jaffle_shop import DatasetConfig, FaultTarget

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

NYC_TAXI = DatasetConfig(
    name="nyc_taxi",
    dbt_project_dir=REPO_ROOT / "pipelines" / "nyc_taxi_dbt",
    duckdb_path=REPO_ROOT / "pipelines" / "nyc_taxi_dbt" / "warehouse.duckdb",
    null_spike_target=FaultTarget(
        raw_table="raw_nyc_taxi",
        column="passenger_count",
        pk_column="trip_id",
        expected_failing_test="not_null_stg_nyc_taxi_passenger_count",
        expected_failing_model="stg_nyc_taxi",
        expected_failing_column="passenger_count",
    ),
    duplicate_ingestion_target=FaultTarget(
        raw_table="raw_nyc_taxi",
        column="trip_id",
        pk_column="trip_id",
        expected_failing_test="unique_stg_nyc_taxi_trip_id",
        expected_failing_model="stg_nyc_taxi",
        expected_failing_column="trip_id",
    ),
    broken_join_dropout_target=FaultTarget(
        raw_table="raw_payment_lookup",
        column="payment_type",
        pk_column="payment_type",
        expected_failing_test="relationships_stg_nyc_taxi_payment_type",
        expected_failing_model="stg_nyc_taxi",
        expected_failing_column="payment_type",
    ),
)
