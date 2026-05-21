"""Typer CLI: `dq-triage <subcommand>`."""

from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(
    name="dq-triage",
    add_completion=False,
    help="DQ Triage Agent CLI.",
)
console = Console()


@app.command()
def version() -> None:
    """Print version."""
    from dq_triage import __version__

    console.print(f"dq-triage v{__version__}")


@app.command()
def demo() -> None:
    """Run a scripted end-to-end demo (Week 5 deliverable)."""
    console.print("[yellow]Demo will land in Week 5. For now, run `make eval-smoke`.[/]")


if __name__ == "__main__":
    app()
