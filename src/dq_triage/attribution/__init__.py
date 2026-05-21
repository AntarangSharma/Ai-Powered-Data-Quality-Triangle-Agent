"""Attribution = the 'where' stage. Walks lineage upstream to source rows.

Current implementations:
  - `ThinAttributor` (W1, deprecated): hand-coded one-level lineage map. Kept
    for A/B comparison only — do not use in new code.
  - `SqlglotWalker` (W2, default): parses compiled dbt SQL with sqlglot and
    walks column lineage across model boundaries. Detects AGG / CAST / WINDOW
    transforms and drops row PKs at AGG boundaries.

Both expose the same `attribute(con, failing_model, failing_column,
failing_pks, failing_pk_column) -> BlameLocation` interface.
"""

from dq_triage.attribution.manifest import Manifest, ManifestNode
from dq_triage.attribution.sqlglot_walker import SqlglotWalker, build_walker
from dq_triage.attribution.thin_attributor import ThinAttributor

__all__ = [
    "Manifest",
    "ManifestNode",
    "SqlglotWalker",
    "ThinAttributor",
    "build_walker",
]
