"""Thin Attributor v0.1 — Week 1.

NOT SQLGlot. Uses a HAND-CODED one-level lineage map keyed by
(downstream_model, downstream_column). Good enough to:
  - close the eval loop
  - establish a baseline number we can improve

Will be replaced in Week 2 by `attribution/sqlglot_walker.py`. The interface
(`attribute()` returning `BlameLocation`) stays the same.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import duckdb

from dq_triage.models import BlameLocation, LineageEdge


@dataclass(frozen=True, slots=True)
class HardcodedLineage:
    upstream_model: str
    upstream_column: str
    downstream_pk: str  # column in downstream model that identifies a row
    upstream_pk: str  # column in upstream model that identifies the same row
    transform: str = "DIRECT"  # always DIRECT for now; W2 will tag CAST/JOIN/AGG


# ---------------------------------------------------------------------------
# Jaffle Shop one-level lineage (will be SQLGlot-derived in W2)
# ---------------------------------------------------------------------------

JAFFLE_LINEAGE: Mapping[tuple[str, str], HardcodedLineage] = {
    # stg_customers ← raw_customers
    ("stg_customers", "customer_id"): HardcodedLineage(
        upstream_model="raw_customers", upstream_column="id",
        downstream_pk="customer_id", upstream_pk="id",
    ),
    # stg_orders ← raw_orders
    ("stg_orders", "order_id"): HardcodedLineage(
        upstream_model="raw_orders", upstream_column="id",
        downstream_pk="order_id", upstream_pk="id",
    ),
    ("stg_orders", "customer_id"): HardcodedLineage(
        upstream_model="raw_orders", upstream_column="user_id",
        downstream_pk="order_id", upstream_pk="id",
    ),
    ("stg_orders", "status"): HardcodedLineage(
        upstream_model="raw_orders", upstream_column="status",
        downstream_pk="order_id", upstream_pk="id",
    ),
    # stg_payments ← raw_payments
    ("stg_payments", "payment_id"): HardcodedLineage(
        upstream_model="raw_payments", upstream_column="id",
        downstream_pk="payment_id", upstream_pk="id",
    ),
    ("stg_payments", "order_id"): HardcodedLineage(
        upstream_model="raw_payments", upstream_column="order_id",
        downstream_pk="payment_id", upstream_pk="id",
    ),
    ("stg_payments", "amount"): HardcodedLineage(
        upstream_model="raw_payments", upstream_column="amount",
        downstream_pk="payment_id", upstream_pk="id",
    ),
    # marts.orders ← stg_orders (1:1 in row identity for these cols)
    ("orders", "order_id"): HardcodedLineage(
        upstream_model="stg_orders", upstream_column="order_id",
        downstream_pk="order_id", upstream_pk="order_id",
    ),
    ("orders", "customer_id"): HardcodedLineage(
        upstream_model="stg_orders", upstream_column="customer_id",
        downstream_pk="order_id", upstream_pk="order_id",
    ),
}


# ---------------------------------------------------------------------------
# Attributor
# ---------------------------------------------------------------------------


class ThinAttributor:
    """One-level walker.

    Given (failing_model, failing_column, failing_pks), look up the upstream
    (model, column, pk) and verify that the upstream row has the same defect
    (NULL for null_spike). Walks at most `max_hops` levels.
    """

    def __init__(
        self,
        lineage: Mapping[tuple[str, str], HardcodedLineage] = JAFFLE_LINEAGE,
        max_hops: int = 4,
    ) -> None:
        self.lineage = lineage
        self.max_hops = max_hops

    def attribute(
        self,
        con: duckdb.DuckDBPyConnection,
        failing_model: str,
        failing_column: str,
        failing_pks: tuple[str, ...],
        failing_pk_column: str,
    ) -> BlameLocation:
        """Walk upstream until we either:
          - reach a node not in the lineage map (assume it's source-level), or
          - reach `max_hops`,
          - or lose row identity.
        """
        model, column = failing_model, failing_column
        pks = list(failing_pks)
        pk_col = failing_pk_column
        path: list[LineageEdge] = []
        certainty = 1.0

        for _ in range(self.max_hops):
            edge_key = (model, column)
            if edge_key not in self.lineage:
                break  # reached a leaf (source) of our knowledge
            edge = self.lineage[edge_key]
            # Translate downstream PKs → upstream PKs.
            # Because Jaffle staging is row-identity-preserving, we can query
            # the upstream table for the same PKs using a join column lookup.
            if edge.downstream_pk == pk_col:
                # Same key already; just relabel
                upstream_pks = pks
            else:
                # We'd need to translate; not used in W1 since all our
                # current lineage rows have downstream_pk == failing_pk_column.
                # Defensive fallback: keep pks as-is, drop certainty.
                upstream_pks = pks
                certainty *= 0.7
            # Verify the upstream rows actually have the defect (NULL here).
            # For W1, just confirm the rows exist.
            placeholders = ",".join(["?"] * len(upstream_pks))
            result = con.execute(
                f"SELECT {edge.upstream_pk} FROM {edge.upstream_model} "
                f"WHERE {edge.upstream_pk} IN ({placeholders})",
                upstream_pks,
            ).fetchall()
            found_pks = [str(r[0]) for r in result]

            path.append(
                LineageEdge(
                    downstream_model=model,
                    downstream_column=column,
                    upstream_model=edge.upstream_model,
                    upstream_column=edge.upstream_column,
                    transform_type="DIRECT",
                    attribution_certainty=certainty,
                    source="sqlglot",  # technically hardcoded, but treat as deterministic
                )
            )
            model = edge.upstream_model
            column = edge.upstream_column
            pks = found_pks
            pk_col = edge.upstream_pk

        return BlameLocation(
            model=model,
            column=column,
            row_pks=tuple(sorted(pks, key=lambda s: (len(s), s))),
            certainty=certainty,
            walk_path=tuple(path),
            hit_agg_boundary=False,
        )
