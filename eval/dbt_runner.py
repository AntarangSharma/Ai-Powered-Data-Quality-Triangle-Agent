"""Tiny shellout wrapper around `dbt` for the eval harness.

Why shellout instead of programmatic API:
  - dbt-core's Python API is unstable across minor versions.
  - We need run_results.json which the CLI writes deterministically.
  - Spawning dbt is fast enough for our scale (~0.5s/build on Jaffle).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console

console = Console()


def _dbt_binary() -> str:
    """Resolve the right `dbt` executable.

    Priority:
      1. $DQ_DBT_BIN env var
      2. dbt next to the running interpreter (i.e. in the venv we're using)
      3. dbt on PATH (last resort — may not have our adapter)
    """
    if env_bin := os.environ.get("DQ_DBT_BIN"):
        return env_bin
    venv_bin = Path(sys.executable).parent / "dbt"
    if venv_bin.exists():
        return str(venv_bin)
    fallback = shutil.which("dbt")
    if fallback:
        return fallback
    raise RuntimeError("Could not locate a `dbt` binary.")


@dataclass(frozen=True, slots=True)
class TestFailure:
    test_name: str  # e.g. "not_null_stg_orders_customer_id"
    model: str  # e.g. "stg_orders"
    column: str | None  # e.g. "customer_id"
    failures_table_fqn: str  # e.g. "main_dbt_test_failures.not_null_stg_orders_customer_id"


@dataclass(frozen=True, slots=True)
class DbtRunResult:
    success_count: int
    fail_count: int
    failures: tuple[TestFailure, ...]
    wall_seconds: float


def _parse_test_name(
    test_name: str, depends_on_models: list[str]
) -> tuple[str, str | None]:
    """Parse a dbt-generated test name into (source_model, column).

    Examples:
      not_null_stg_orders_customer_id              -> (stg_orders, customer_id)
      unique_stg_orders_order_id                   -> (stg_orders, order_id)
      relationships_stg_orders_customer_id__customer_id__ref_stg_customers_
                                                   -> (stg_orders, customer_id)

    For relationships tests, two models appear in `depends_on`: the source
    (the one whose column owns the FK) and the target (the referenced model).
    We pick the source by trying every prefix `<test_kind>_<model>_` and
    accepting the first that matches.
    """
    if not depends_on_models:
        return "?", None
    kinds = ("not_null", "unique", "accepted_values", "relationships")
    # Try every (kind, model) combo. Pick the *longest* matching model name
    # to disambiguate when one is a prefix of another (e.g. `stg` vs
    # `stg_orders` in a hypothetical project).
    candidates = []
    for kind in kinds:
        for m in depends_on_models:
            pref = f"{kind}_{m}_"
            if test_name.startswith(pref):
                rest = test_name[len(pref):]
                # accepted_values + relationships test names get hashed/truncated
                # after the column — split on '__' to recover just the column.
                column = rest if "__" not in rest else rest.split("__")[0]
                candidates.append((len(m), m, column or None))
    if candidates:
        # longest-model match wins
        candidates.sort(reverse=True)
        _, model, column = candidates[0]
        return model, column
    # Fallback: first model in depends_on, no column.
    return depends_on_models[0], None


def run_dbt(
    project_dir: Path,
    command: str,
    extra_args: list[str] | None = None,
    duckdb_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DBT_PROFILES_DIR"] = str(project_dir)
    if duckdb_path is not None:
        env["DQ_DUCKDB_PATH"] = str(duckdb_path)
    cmd = [_dbt_binary(), command, *(extra_args or [])]
    result = subprocess.run(
        cmd,
        cwd=str(project_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    return result


def seed(project_dir: Path, duckdb_path: Path) -> None:
    res = run_dbt(project_dir, "seed", ["--full-refresh", "-q"], duckdb_path=duckdb_path)
    if res.returncode != 0:
        raise RuntimeError(f"dbt seed failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")


def build(project_dir: Path, duckdb_path: Path) -> DbtRunResult:
    """Run models then tests separately (so we do NOT re-run `seed`, which
    would wipe a fault that was applied between the seed and the build).

    Captures the *test* run_results since that's where failures live.
    """
    # 1. Build the model graph (views/tables). Skip tests.
    res_run = run_dbt(
        project_dir,
        "run",
        ["-q"],
        duckdb_path=duckdb_path,
    )
    # `dbt run` returns 0 even with no models compiled.
    # 2. Run the tests separately. Non-zero exit on failures is expected.
    res = run_dbt(
        project_dir,
        "test",
        ["--indirect-selection=eager", "-q"],
        duckdb_path=duckdb_path,
    )
    _ = res_run  # quiet linter; useful for debug
    run_results_path = project_dir / "target" / "run_results.json"
    if not run_results_path.exists():
        raise RuntimeError(
            f"dbt build produced no run_results.json:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
        )
    with run_results_path.open() as fh:
        run_results = json.load(fh)
    elapsed = float(run_results.get("elapsed_time", 0.0))

    # Also load manifest to map test → ref'd model.
    manifest_path = project_dir / "target" / "manifest.json"
    with manifest_path.open() as fh:
        manifest = json.load(fh)

    failures: list[TestFailure] = []
    successes = 0
    for r in run_results["results"]:
        unique_id = r["unique_id"]
        if not unique_id.startswith("test."):
            continue
        if r["status"] == "pass":
            successes += 1
            continue
        if r["status"] not in {"fail", "error", "warn"}:
            continue
        node = manifest["nodes"].get(unique_id, {})
        test_name = node.get("name", unique_id.split(".")[-1])
        depends_on = node.get("depends_on", {}).get("nodes", [])
        ref_models = [
            dep.split(".")[-1] for dep in depends_on if dep.startswith("model.")
        ]
        model, column = _parse_test_name(test_name, ref_models)
        relation = r.get("relation_name") or node.get("relation_name") or ""
        # relation_name is the failures table when store_failures=true.
        failures_fqn = relation.replace('"', "")
        failures.append(
            TestFailure(
                test_name=test_name,
                model=model,
                column=column,
                failures_table_fqn=failures_fqn,
            )
        )

    return DbtRunResult(
        success_count=successes,
        fail_count=len(failures),
        failures=tuple(failures),
        wall_seconds=elapsed,
    )
