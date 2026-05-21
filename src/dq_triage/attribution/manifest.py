"""dbt manifest loader.

Reads `target/manifest.json` and provides:
  - lookup of compiled SQL by model unique_id
  - resolution of a fully-qualified relation (schema.table) to a manifest node
  - classification of nodes as model | seed | source | unknown

Why we don't trust `node['compiled_code']`: it's only populated *after* `dbt
compile`, and `dbt parse` (which is fast) doesn't write it. But the file at
`target/compiled/<project>/<original_file_path>` is written by both `dbt run`
and `dbt compile`, so we read from disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal

NodeKind = Literal["model", "seed", "source", "unknown"]


@dataclass(frozen=True, slots=True)
class ManifestNode:
    unique_id: str
    name: str
    schema: str
    alias: str
    database: str
    kind: NodeKind
    original_file_path: str
    # For models only — the compiled SQL on disk. None for seeds/sources.
    compiled_sql_path: Path | None


class Manifest:
    """Read-only view over `target/manifest.json` for lineage resolution."""

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        path = project_dir / "target" / "manifest.json"
        if not path.exists():
            raise FileNotFoundError(
                f"manifest.json not found at {path}. "
                f"Run `dbt parse` or `dbt run` first."
            )
        with path.open() as fh:
            self._raw = json.load(fh)
        self._project_name: str = self._raw["metadata"]["project_name"]

    @cached_property
    def nodes(self) -> dict[str, ManifestNode]:
        """All resolvable nodes keyed by unique_id."""
        out: dict[str, ManifestNode] = {}
        for uid, n in self._raw["nodes"].items():
            kind: NodeKind
            if uid.startswith("model."):
                kind = "model"
            elif uid.startswith("seed."):
                kind = "seed"
            else:
                continue  # tests, snapshots, exposures — not lineage targets
            compiled_path: Path | None = None
            if kind == "model":
                compiled_path = (
                    self.project_dir
                    / "target"
                    / "compiled"
                    / self._project_name
                    / n["original_file_path"]
                )
            out[uid] = ManifestNode(
                unique_id=uid,
                name=n["name"],
                schema=n["schema"],
                alias=n.get("alias") or n["name"],
                database=n.get("database") or "",
                kind=kind,
                original_file_path=n["original_file_path"],
                compiled_sql_path=compiled_path,
            )
        for uid, s in self._raw.get("sources", {}).items():
            out[uid] = ManifestNode(
                unique_id=uid,
                name=s["name"],
                schema=s["schema"],
                alias=s.get("identifier") or s["name"],
                database=s.get("database") or "",
                kind="source",
                original_file_path=s.get("original_file_path", ""),
                compiled_sql_path=None,
            )
        return out

    @cached_property
    def by_relation(self) -> dict[tuple[str, str], ManifestNode]:
        """Lookup by (schema_lower, table_lower). dbt-duckdb may emit relations
        as `"warehouse"."main_staging"."stg_orders"` — we ignore the database
        part because DuckDB schemas are unique per file."""
        idx: dict[tuple[str, str], ManifestNode] = {}
        for n in self.nodes.values():
            idx[(n.schema.lower(), n.alias.lower())] = n
        return idx

    @cached_property
    def by_name(self) -> dict[str, ManifestNode]:
        """Lookup by model/seed name (unique within a dbt project)."""
        return {n.name: n for n in self.nodes.values()}

    def resolve(self, schema: str | None, table: str) -> ManifestNode | None:
        """Find a node by (schema, table). Falls back to name-only if schema is None.

        dbt-DuckDB lowercase-normalizes identifiers. We do the same.
        """
        table_low = table.lower()
        if schema:
            hit = self.by_relation.get((schema.lower(), table_low))
            if hit is not None:
                return hit
        # Fallback: name-only resolution.
        return self.by_name.get(table_low)

    def compiled_sql(self, node: ManifestNode) -> str:
        """Read compiled SQL for a model. Raises if missing."""
        if node.kind != "model":
            raise ValueError(f"Cannot read compiled SQL for {node.kind} node {node.unique_id}")
        if node.compiled_sql_path is None or not node.compiled_sql_path.exists():
            raise FileNotFoundError(
                f"Compiled SQL not found at {node.compiled_sql_path}. "
                f"Run `dbt run` or `dbt compile` first."
            )
        return node.compiled_sql_path.read_text()
