"""Per-dataset configuration (fault targets, dbt project location, etc.)."""

from eval.datasets.jaffle_shop import JAFFLE_SHOP
from eval.datasets.nyc_taxi import NYC_TAXI
from eval.datasets.tpch import TPCH

__all__ = ["JAFFLE_SHOP", "NYC_TAXI", "TPCH"]
