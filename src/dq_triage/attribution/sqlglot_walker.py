"""SQLGlot-based column lineage walker (Week 2 replacement for ThinAttributor).

Algorithm:
  given (model, column, row_pks):
    1. Read compiled SQL for `model` from the dbt manifest.
    2. Call `sqlglot.lineage.lineage(column, sql)` → a tree of column refs.
    3. Walk down the tree to the deepest non-`*` node. That node's
       column-after-the-dot is the upstream column, and its source SELECT's
       FROM clause names the upstream table.
    4. If the upstream table is another dbt model → recurse.
       If it's a seed/source → terminate.
    5. While walking, detect when we cross an aggregation boundary
       (GROUP BY, or aggregate fn in projection). Row-PK identity is broken
       past that point, so we stop propagating PKs and set
       `hit_agg_boundary=True`.

What this walker does NOT yet do (parked for Week 2.5/3):
  - JOIN_KEY transform tagging (we tag everything DIRECT/CAST/AGG today)
  - Window functions
  - Macros / UDFs (sqlglot renders pre-Jinja, so most macros are inlined)
  - LLM fallback when sqlglot fails — that's Week 4

It must, however, **match the W1 ThinAttributor numbers on null_spike** to
count as progress. The runner can swap in `SqlglotWalker` for `ThinAttributor`
because they share an `attribute()` signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
from sqlglot import exp
from sqlglot import lineage as sg_lineage

from dq_triage.attribution.manifest import Manifest, ManifestNode
from dq_triage.models import BlameLocation, LineageEdge


@dataclass(frozen=True, slots=True)
class _Step:
    """One hop of the column-lineage walk."""

    downstream_model: str
    downstream_column: str
    upstream_model: str
    upstream_column: str
    transform: str  # DIRECT | CAST | AGG | JOIN_KEY | WINDOW | OPAQUE
    crossed_agg: bool


# ---------------------------------------------------------------------------
# AST helpers — kept small and pure for unit-testability.
# ---------------------------------------------------------------------------


def _select_has_aggregation(select: exp.Expression) -> bool:
    """True if this Select has a GROUP BY or any aggregate function in its
    projection list (or its top-level WHERE/HAVING for completeness)."""
    if not isinstance(select, exp.Select):
        return False
    if select.args.get("group") is not None:
        return True
    return any(
        next(iter(proj.find_all(exp.AggFunc)), None) is not None
        for proj in select.expressions or []
    )


def _select_has_cast(select: exp.Expression) -> bool:
    if not isinstance(select, exp.Select):
        return False
    return any(
        next(iter(proj.find_all(exp.Cast, exp.TryCast)), None) is not None
        for proj in select.expressions or []
    )


def _select_has_window(select: exp.Expression) -> bool:
    if not isinstance(select, exp.Select):
        return False
    return any(
        next(iter(proj.find_all(exp.Window)), None) is not None for proj in select.expressions or []
    )


def _from_table(node_source: exp.Expression) -> exp.Table | None:
    """Return the upstream physical Table reference.

    `node_source` is whatever sqlglot.lineage gave us as the .source of a
    terminal lineage node. Two shapes occur in practice:

      1. A `Select` (e.g. ``SELECT * FROM raw_t``) — pull the first Table.
      2. A `Table` directly (the column is read straight from a base table
         without any wrapping projection, e.g. ``SELECT col FROM t`` with no
         CTEs) — return it as-is.

    sqlglot.lineage has already disambiguated which side of a JOIN the column
    came from before handing us this node, so taking the first Table is safe.
    """
    if isinstance(node_source, exp.Table):
        return node_source
    if not isinstance(node_source, exp.Select):
        return None
    if node_source.args.get("from") is None:
        return None
    for tbl in node_source.find_all(exp.Table):
        return tbl
    return None


def _classify_transform(select: exp.Expression) -> str:
    """Best-effort transform classification at this hop.

    Order matters: AGG before CAST before WINDOW so that 'SUM(CAST(...))' is
    tagged AGG (the row-identity-breaking property dominates)."""
    if _select_has_aggregation(select):
        return "AGG"
    if _select_has_window(select):
        return "WINDOW"
    if _select_has_cast(select):
        return "CAST"
    return "DIRECT"


# ---------------------------------------------------------------------------
# Walker
# ---------------------------------------------------------------------------


class SqlglotWalker:
    """Drop-in replacement for ThinAttributor. Same `attribute()` interface."""

    def __init__(self, manifest: Manifest, dialect: str = "duckdb", max_hops: int = 8) -> None:
        self.manifest = manifest
        self.dialect = dialect
        self.max_hops = max_hops

    # ----- public API ------------------------------------------------------

    def attribute(
        self,
        con: duckdb.DuckDBPyConnection,
        failing_model: str,
        failing_column: str,
        failing_pks: tuple[str, ...],
        failing_pk_column: str,
    ) -> BlameLocation:
        """Walk column lineage upstream from (failing_model, failing_column)."""
        steps: list[_Step] = []
        cur_model_name = failing_model
        cur_column = failing_column
        hit_agg = False

        for _ in range(self.max_hops):
            node = self.manifest.by_name.get(cur_model_name)
            if node is None or node.kind != "model":
                # Reached a seed/source or unknown — stop.
                break
            try:
                hop = self._one_hop(node, cur_column)
            except _LineageDeadEnd:
                break
            steps.append(hop)
            if hop.crossed_agg:
                hit_agg = True
            cur_model_name = hop.upstream_model
            cur_column = hop.upstream_column
            # If we just hopped *into* a seed/source we'll exit on next iter.

        # Build path of LineageEdge objects.
        path = tuple(
            LineageEdge(
                downstream_model=s.downstream_model,
                downstream_column=s.downstream_column,
                upstream_model=s.upstream_model,
                upstream_column=s.upstream_column,
                transform_type=s.transform,
                attribution_certainty=1.0 if not s.crossed_agg else 0.6,
                source="sqlglot",
            )
            for s in steps
        )

        # PK propagation. For W2 we keep it simple:
        #   - if no AGG crossed and the failing PKs were given, carry them through
        #     unchanged (Jaffle staging is row-identity-preserving).
        #   - if AGG crossed, drop PKs.
        carried_pks: tuple[str, ...] = (
            () if hit_agg else tuple(sorted(failing_pks, key=lambda s: (len(s), s)))
        )

        certainty = 1.0 if not hit_agg else 0.6
        if not steps:
            # No hop happened — we're already at a seed/source/unknown.
            return BlameLocation(
                model=failing_model,
                column=failing_column,
                row_pks=tuple(sorted(failing_pks, key=lambda s: (len(s), s))),
                certainty=0.5,
                walk_path=(),
                hit_agg_boundary=False,
            )

        terminal = steps[-1]
        # Optional defect verification — keep cheap, just confirm rows exist
        # in the upstream table when we still have PKs to check.
        if carried_pks and terminal.transform == "DIRECT":
            self._verify_rows_exist(con, terminal.upstream_model, failing_pk_column, carried_pks)

        return BlameLocation(
            model=terminal.upstream_model,
            column=terminal.upstream_column,
            row_pks=carried_pks,
            certainty=certainty,
            walk_path=path,
            hit_agg_boundary=hit_agg,
        )

    # ----- one hop ---------------------------------------------------------

    def _one_hop(self, node: ManifestNode, column: str) -> _Step:
        """Resolve one upstream hop for `column` within model `node`."""
        sql = self.manifest.compiled_sql(node)
        try:
            root = sg_lineage.lineage(column, sql, dialect=self.dialect)
        except Exception as e:  # sqlglot raises various — wrap as dead end.
            raise _LineageDeadEnd(f"sqlglot failed on {node.name}.{column}: {e}") from e

        # Walk down to the deepest non-`*` node. Track agg/cast/window crossings.
        terminal = root
        crossed_agg = False
        crossed_cast = False
        crossed_window = False
        cur = root
        while True:
            # Inspect cur's source for transform character.
            if _select_has_aggregation(cur.source):
                crossed_agg = True
            elif _select_has_window(cur.source):
                crossed_window = True
            elif _select_has_cast(cur.source):
                crossed_cast = True
            non_star_children = [d for d in cur.downstream if d.name != "*"]
            if not non_star_children:
                terminal = cur
                break
            cur = non_star_children[0]

        # Extract upstream column name.
        if "." in terminal.name:
            _, upstream_col = terminal.name.rsplit(".", 1)
        else:
            upstream_col = terminal.name

        # Extract upstream table from terminal.source's FROM clause.
        tbl = _from_table(terminal.source)
        if tbl is None:
            raise _LineageDeadEnd(f"No FROM table at terminal for {node.name}.{column}")
        # Resolve to a manifest node (model/seed/source) — by schema+name where possible.
        # dbt-DuckDB emits `database.schema.table`; sqlglot stores schema under the "db" arg.
        db_arg = tbl.args.get("db")
        upstream_schema = db_arg.name if db_arg is not None else None
        upstream_name = tbl.name
        upstream_node = self.manifest.resolve(upstream_schema, upstream_name)
        upstream_model_name = upstream_node.name if upstream_node else upstream_name

        if crossed_agg:
            transform = "AGG"
        elif crossed_window:
            transform = "WINDOW"
        elif crossed_cast:
            transform = "CAST"
        else:
            transform = "DIRECT"

        return _Step(
            downstream_model=node.name,
            downstream_column=column,
            upstream_model=upstream_model_name,
            upstream_column=upstream_col,
            transform=transform,
            crossed_agg=crossed_agg,
        )

    # ----- verification ----------------------------------------------------

    def _verify_rows_exist(
        self,
        con: duckdb.DuckDBPyConnection,
        upstream_model: str,
        pk_col: str,
        pks: tuple[str, ...],
    ) -> None:
        """Best-effort: confirm rows still exist upstream. Not used to gate
        the blame today; primarily a hook for Week 3 defect re-checks."""
        try:
            placeholders = ",".join(["?"] * len(pks))
            con.execute(
                f"SELECT {pk_col} FROM {upstream_model} WHERE {pk_col} IN ({placeholders})",
                list(pks),
            ).fetchall()
        except duckdb.Error:
            # Swallow — we don't fail attribution on a missing column/table.
            return


class _LineageDeadEnd(RuntimeError):
    """Raised when sqlglot cannot follow the column any further. Treated as
    a normal terminal condition by the walker (not an error)."""


# ---------------------------------------------------------------------------
# Construction helper
# ---------------------------------------------------------------------------


def build_walker(project_dir: Path, dialect: str = "duckdb") -> SqlglotWalker:
    """Convenience constructor: load manifest from `project_dir` and return a
    ready-to-use walker."""
    return SqlglotWalker(Manifest(project_dir), dialect=dialect)
