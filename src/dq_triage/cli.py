"""Typer CLI: ``dq-triage <subcommand>``.

Three commands today:

  * ``version``      — print the package version.
  * ``triage``       — run the full pipeline against the most recent dbt
                       test run (or a specific failing test) and persist
                       the resulting Incident.
  * ``incidents``    — list / show persisted incidents from the store.

The CLI is a *thin* wrapper around :mod:`dq_triage.agent.orchestrator`
and :mod:`dq_triage.store`; all real logic lives there so the same
pipeline can be invoked from a FastAPI handler, an Airflow on-failure
hook, etc.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from dq_triage.models import Incident

app = typer.Typer(
    name="dq-triage",
    add_completion=False,
    help="DQ Triage Agent CLI.",
)
console = Console()


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the installed dq-triage version."""
    from dq_triage import __version__

    console.print(f"dq-triage v{__version__}")


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------


@app.command()
def triage(
    project: Path = typer.Option(
        ...,
        "--project",
        "-p",
        help="Path to the dbt project directory (the one containing dbt_project.yml).",
        exists=True,
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    duckdb_path: Path = typer.Option(
        ...,
        "--duckdb",
        "-d",
        help="Path to the DuckDB file the dbt project targets.",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    test: str | None = typer.Option(
        None,
        "--test",
        "-t",
        help=(
            "Test name (e.g. 'not_null_stg_orders_user_id'). If omitted, the "
            "first failing test in target/run_results.json is triaged."
        ),
    ),
    persist: bool = typer.Option(
        True,
        "--persist/--no-persist",
        help="Write the resulting Incident to the configured store (DQ_DATABASE_URL).",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Print the Incident as JSON instead of a rich table (machine-readable).",
    ),
) -> None:
    """Triage a failing dbt test end-to-end.

    Expects dbt to have already run in ``project`` (we read
    ``target/run_results.json`` and ``target/manifest.json``). Returns
    exit code 0 if an Incident was produced, 2 if there were no failing
    tests to triage, 1 on any other error.
    """
    from dq_triage.agent.orchestrator import (
        load_failing_tests,
        triage_and_persist,
    )
    from dq_triage.agent.orchestrator import (
        triage as _triage,
    )

    failing = load_failing_tests(project)
    if not failing:
        console.print(
            "[yellow]No failing tests found in target/run_results.json. Run `dbt build` first.[/]"
        )
        raise typer.Exit(code=2)

    chosen = None
    if test is not None:
        for f in failing:
            if f.test_name == test or f.test_name.startswith(test):
                chosen = f
                break
        if chosen is None:
            console.print(
                f"[red]No failing test matched name '{test}'. "
                f"Available: {', '.join(f.test_name for f in failing)}[/]"
            )
            raise typer.Exit(code=1)
    else:
        # Prefer staging-level tests (closer to root cause), then alphabetical.
        chosen = sorted(
            failing,
            key=lambda f: (0 if f.model.startswith("stg_") else 1, f.model, f.test_name),
        )[0]

    fn = triage_and_persist if persist else _triage
    incident = fn(project_dir=project, duckdb_path=duckdb_path, failing_test=chosen)

    if json_out:
        console.print_json(incident.model_dump_json())
    else:
        _print_incident(incident)


def _print_incident(incident: Incident) -> None:
    """Pretty-print an Incident with rich tables."""
    from dq_triage.models import Verdict

    verdict_colour = {
        Verdict.AUTO: "green",
        Verdict.TWO_CANDIDATE: "yellow",
        Verdict.TRIAGE_ONLY: "red",
    }[incident.verdict_type]

    console.rule(f"[bold]{incident.incident_id}")
    console.print(
        f"[bold]Failing test:[/] {incident.failing_test_name}  "
        f"([dim]{incident.failing_model}.{incident.failing_column or '*'}[/])"
    )
    console.print(
        f"[bold]Failing rows:[/] {incident.failing_row_count}   "
        f"[bold]Latency:[/] {incident.time_to_verdict_seconds:.2f}s   "
        f"[bold]Verdict:[/] [{verdict_colour}]{incident.verdict_type.value}[/]"
    )

    blame = incident.blame_location
    console.print()
    console.print(
        f"[bold]Blame:[/] {blame.model}.{blame.column or '?'}  "
        f"(certainty {blame.certainty:.2f}, {len(blame.row_pks)} row(s))"
    )
    if incident.final_verdict is not None:
        v = incident.final_verdict
        console.print(
            f"[bold]Cause:[/] [{verdict_colour}]{v.cause_class.value}[/]  "
            f"(confidence {v.confidence:.2f})"
        )
        console.print(f"[bold]Why:[/] {v.evidence_summary}")
        console.print(f"[bold]Fix:[/] {v.suggested_one_line_fix}")
    else:
        console.print("[yellow]No confident verdict — see ranked candidates below.[/]")

    tbl = Table(title="Ranked cause classes", show_lines=False)
    tbl.add_column("rank", justify="right")
    tbl.add_column("class")
    tbl.add_column("score", justify="right")
    for i, cs in enumerate(incident.class_scores, 1):
        tbl.add_row(str(i), cs.cause_class.value, f"{cs.score:.2f}")
    console.print(tbl)


# ---------------------------------------------------------------------------
# incidents (list / show)
# ---------------------------------------------------------------------------


incidents_app = typer.Typer(help="Inspect persisted incidents.")
app.add_typer(incidents_app, name="incidents")


@incidents_app.command("list")
def incidents_list(
    limit: int = typer.Option(20, "--limit", "-n", help="Max rows."),
) -> None:
    """List the most recent persisted incidents."""
    from dq_triage.store import list_incidents

    rows = list_incidents(limit=limit)
    if not rows:
        console.print("[dim]No incidents persisted.[/]")
        return
    tbl = Table(show_lines=False)
    tbl.add_column("incident_id")
    tbl.add_column("created_at")
    tbl.add_column("model")
    tbl.add_column("verdict")
    tbl.add_column("cause")
    for r in rows:
        top_cause = (
            r.final_verdict.cause_class.value
            if r.final_verdict is not None
            else (r.class_scores[0].cause_class.value if r.class_scores else "-")
        )
        tbl.add_row(
            r.incident_id,
            r.created_at.isoformat(timespec="seconds"),
            r.failing_model,
            r.verdict_type.value,
            top_cause,
        )
    console.print(tbl)


@incidents_app.command("show")
def incidents_show(
    incident_id: str = typer.Argument(..., help="Incident ID to display."),
    json_out: bool = typer.Option(False, "--json", help="Print as JSON."),
) -> None:
    """Show the full record for one incident."""
    from dq_triage.store import load_incident

    incident = load_incident(incident_id)
    if incident is None:
        console.print(f"[red]incident {incident_id!r} not found[/]")
        raise typer.Exit(code=1)
    if json_out:
        console.print_json(incident.model_dump_json())
    else:
        _print_incident(incident)


if __name__ == "__main__":
    app()
