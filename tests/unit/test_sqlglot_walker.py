"""Unit tests for the SQLGlot column-lineage walker.

Strategy: build a small fake `Manifest` in-memory rather than relying on a
real dbt project on disk. This keeps tests fast (<1s) and isolates the walker
from dbt/duckdb.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
import sqlglot

from dq_triage.attribution.manifest import ManifestNode
from dq_triage.attribution.sqlglot_walker import (
    SqlglotWalker,
    _classify_transform,
    _from_table,
    _select_has_aggregation,
    _select_has_cast,
    _select_has_window,
)

# ---------------------------------------------------------------------------
# Fake Manifest — implements only what the walker reads.
# ---------------------------------------------------------------------------


@dataclass
class FakeManifest:
    by_name: Mapping[str, ManifestNode]
    _sql: Mapping[str, str]

    def compiled_sql(self, node: ManifestNode) -> str:
        return self._sql[node.name]

    def resolve(self, schema: str | None, table: str) -> ManifestNode | None:
        return self.by_name.get(table.lower())


def _model(name: str, schema: str = "main_staging") -> ManifestNode:
    return ManifestNode(
        unique_id=f"model.test.{name}",
        name=name,
        schema=schema,
        alias=name,
        database="warehouse",
        kind="model",
        original_file_path=f"models/{name}.sql",
        compiled_sql_path=Path(f"target/compiled/test/models/{name}.sql"),
    )


def _seed(name: str, schema: str = "main") -> ManifestNode:
    return ManifestNode(
        unique_id=f"seed.test.{name}",
        name=name,
        schema=schema,
        alias=name,
        database="warehouse",
        kind="seed",
        original_file_path=f"seeds/{name}.csv",
        compiled_sql_path=None,
    )


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def test_select_has_aggregation_group_by() -> None:
    sel = sqlglot.parse_one("SELECT x, SUM(y) AS y FROM t GROUP BY x", dialect="duckdb")
    assert _select_has_aggregation(sel) is True


def test_select_has_aggregation_implicit_agg() -> None:
    # No GROUP BY but an aggregate fn in projection.
    sel = sqlglot.parse_one("SELECT COUNT(*) AS c FROM t", dialect="duckdb")
    assert _select_has_aggregation(sel) is True


def test_select_has_aggregation_plain_select_is_false() -> None:
    sel = sqlglot.parse_one("SELECT a, b FROM t", dialect="duckdb")
    assert _select_has_aggregation(sel) is False


def test_select_has_cast_explicit_cast() -> None:
    sel = sqlglot.parse_one("SELECT CAST(x AS INT) AS x FROM t", dialect="duckdb")
    assert _select_has_cast(sel) is True


def test_select_has_cast_try_cast() -> None:
    sel = sqlglot.parse_one("SELECT TRY_CAST(x AS INT) AS x FROM t", dialect="duckdb")
    assert _select_has_cast(sel) is True


def test_select_has_window() -> None:
    sel = sqlglot.parse_one(
        "SELECT ROW_NUMBER() OVER (PARTITION BY x ORDER BY y) AS rn FROM t",
        dialect="duckdb",
    )
    assert _select_has_window(sel) is True


def test_classify_transform_prefers_agg_over_cast() -> None:
    # SUM(CAST(...)) — AGG dominates because it breaks row identity.
    sel = sqlglot.parse_one("SELECT SUM(CAST(x AS INT)) AS y FROM t GROUP BY z", dialect="duckdb")
    assert _classify_transform(sel) == "AGG"


def test_classify_transform_cast_when_no_agg() -> None:
    sel = sqlglot.parse_one("SELECT CAST(x AS INT) AS x FROM t", dialect="duckdb")
    assert _classify_transform(sel) == "CAST"


def test_classify_transform_direct() -> None:
    sel = sqlglot.parse_one("SELECT x, y FROM t", dialect="duckdb")
    assert _classify_transform(sel) == "DIRECT"


def test_from_table_returns_first_table() -> None:
    sel = sqlglot.parse_one("SELECT a FROM customers JOIN orders USING (id)", dialect="duckdb")
    tbl = _from_table(sel)
    assert tbl is not None
    assert tbl.name == "customers"


# ---------------------------------------------------------------------------
# End-to-end walker tests (using fake manifests).
# ---------------------------------------------------------------------------


class _FakeWalker(SqlglotWalker):
    """SqlglotWalker but skips the warehouse defect-verification query so we
    don't need a real DuckDB connection in unit tests."""

    def _verify_rows_exist(self, *_a, **_kw) -> None:  # type: ignore[override]
        return None


def _build_walker(manifest: FakeManifest) -> _FakeWalker:
    w = _FakeWalker.__new__(_FakeWalker)
    # Bypass __init__'s Manifest type — duck typing is sufficient for the API
    # surface the walker actually uses (by_name + compiled_sql + resolve).
    w.manifest = manifest  # type: ignore[assignment]
    w.dialect = "duckdb"
    w.max_hops = 8
    return w


def test_walker_single_rename() -> None:
    """stg.col_renamed ← raw.original_col through a simple alias."""
    manifest = FakeManifest(
        by_name={
            "stg": _model("stg"),
            "raw_t": _seed("raw_t"),
        },
        _sql={
            "stg": 'SELECT user_id AS customer_id FROM "warehouse"."main"."raw_t"',
        },
    )
    walker = _build_walker(manifest)
    blame = walker.attribute(
        con=None,  # type: ignore[arg-type]
        failing_model="stg",
        failing_column="customer_id",
        failing_pks=("a", "b"),
        failing_pk_column="customer_id",
    )
    assert blame.model == "raw_t"
    assert blame.column == "user_id"
    assert blame.hit_agg_boundary is False
    assert blame.row_pks == ("a", "b")
    assert len(blame.walk_path) == 1
    edge = blame.walk_path[0]
    assert edge.transform_type == "DIRECT"
    assert edge.source == "sqlglot"


def test_walker_recurses_across_models() -> None:
    """mart.col -> stg.col -> raw.col"""
    manifest = FakeManifest(
        by_name={
            "mart": _model("mart", schema="main_marts"),
            "stg": _model("stg"),
            "raw_t": _seed("raw_t"),
        },
        _sql={
            "mart": 'SELECT customer_id FROM "warehouse"."main_staging"."stg"',
            "stg": 'SELECT user_id AS customer_id FROM "warehouse"."main"."raw_t"',
        },
    )
    walker = _build_walker(manifest)
    blame = walker.attribute(
        con=None,  # type: ignore[arg-type]
        failing_model="mart",
        failing_column="customer_id",
        failing_pks=("1",),
        failing_pk_column="customer_id",
    )
    assert blame.model == "raw_t"
    assert blame.column == "user_id"
    assert [(e.downstream_model, e.upstream_model) for e in blame.walk_path] == [
        ("mart", "stg"),
        ("stg", "raw_t"),
    ]


def test_walker_marks_agg_boundary_and_drops_pks() -> None:
    manifest = FakeManifest(
        by_name={
            "mart": _model("mart", schema="main_marts"),
            "stg_p": _model("stg_p"),
            "raw_p": _seed("raw_p"),
        },
        _sql={
            "mart": (
                "SELECT order_id, SUM(amount) AS amount "
                'FROM "warehouse"."main_staging"."stg_p" GROUP BY order_id'
            ),
            "stg_p": 'SELECT amount FROM "warehouse"."main"."raw_p"',
        },
    )
    walker = _build_walker(manifest)
    blame = walker.attribute(
        con=None,  # type: ignore[arg-type]
        failing_model="mart",
        failing_column="amount",
        failing_pks=("1", "2"),
        failing_pk_column="order_id",
    )
    assert blame.hit_agg_boundary is True
    assert blame.row_pks == ()  # AGG breaks row identity
    assert blame.certainty < 1.0
    # First hop was the AGG (mart -> stg_p), then DIRECT to raw_p.
    transforms = [e.transform_type for e in blame.walk_path]
    assert transforms[0] == "AGG"


def test_walker_stops_at_seed() -> None:
    """A seed has no compiled SQL, so the walker should terminate cleanly."""
    manifest = FakeManifest(
        by_name={
            "stg": _model("stg"),
            "raw_t": _seed("raw_t"),
        },
        _sql={
            "stg": 'SELECT x FROM "warehouse"."main"."raw_t"',
        },
    )
    walker = _build_walker(manifest)
    blame = walker.attribute(
        con=None,  # type: ignore[arg-type]
        failing_model="stg",
        failing_column="x",
        failing_pks=("1",),
        failing_pk_column="x",
    )
    assert blame.model == "raw_t"
    # No further hop attempted into the seed.
    assert len(blame.walk_path) == 1


def test_walker_cast_transform_tagged() -> None:
    manifest = FakeManifest(
        by_name={
            "stg": _model("stg"),
            "raw_t": _seed("raw_t"),
        },
        _sql={
            "stg": 'SELECT CAST(amount AS INT) AS amount FROM "warehouse"."main"."raw_t"',
        },
    )
    walker = _build_walker(manifest)
    blame = walker.attribute(
        con=None,  # type: ignore[arg-type]
        failing_model="stg",
        failing_column="amount",
        failing_pks=(),
        failing_pk_column="id",
    )
    assert blame.walk_path[0].transform_type == "CAST"
    assert blame.hit_agg_boundary is False


def test_walker_handles_cte_chains() -> None:
    """Mimics dbt's typical 'source -> renamed -> select *' staging pattern."""
    sql = """
    WITH source AS (SELECT * FROM "warehouse"."main"."raw_t"),
         renamed AS (SELECT id AS pk, val AS v FROM source)
    SELECT * FROM renamed
    """
    manifest = FakeManifest(
        by_name={
            "stg": _model("stg"),
            "raw_t": _seed("raw_t"),
        },
        _sql={"stg": sql},
    )
    walker = _build_walker(manifest)
    blame = walker.attribute(
        con=None,  # type: ignore[arg-type]
        failing_model="stg",
        failing_column="v",
        failing_pks=("x",),
        failing_pk_column="pk",
    )
    assert blame.model == "raw_t"
    assert blame.column == "val"


# ---------------------------------------------------------------------------
# Regression tests: the actual W1 lineage we shipped must still resolve.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "column", "expected_model", "expected_column"),
    [
        ("stg_orders", "customer_id", "raw_orders", "user_id"),
        ("stg_orders", "order_id", "raw_orders", "id"),
        ("stg_customers", "customer_id", "raw_customers", "id"),
        ("stg_payments", "amount", "raw_payments", "amount"),
        ("orders", "customer_id", "raw_orders", "user_id"),
        ("orders", "order_id", "raw_orders", "id"),
    ],
)
def test_jaffle_lineage_resolves(
    model: str, column: str, expected_model: str, expected_column: str
) -> None:
    """Smoke-resolve against the real Jaffle compiled SQL on disk."""
    project_dir = Path(__file__).resolve().parents[2] / "pipelines" / "jaffle_shop"
    if not (project_dir / "target" / "manifest.json").exists():
        pytest.skip("Jaffle target/manifest.json missing — run `dbt run` first.")
    from dq_triage.attribution.sqlglot_walker import build_walker

    walker = build_walker(project_dir)
    blame = walker.attribute(
        con=None,  # type: ignore[arg-type]
        failing_model=model,
        failing_column=column,
        failing_pks=(),
        failing_pk_column="id",
    )
    assert blame.model == expected_model
    assert blame.column == expected_column
