"""Housekeeper CLI entry point.

The CLI is the single user-facing surface for ops tasks: ``housekeeper doctor``
(health checks), ``housekeeper tail`` (event stream), etc. Subcommands are
added in later phases. For now we only expose ``version`` and a placeholder
``doctor`` so the shell entry point is wired and testable.
"""

from __future__ import annotations

import platform
import sys

import typer
from rich.console import Console

from housekeeper import __version__

app = typer.Typer(
    name="housekeeper",
    help="Local-first AI agent that watches an IP camera and talks to you.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


@app.command()
def version() -> None:
    """Print the Housekeeper version."""
    console.print(f"housekeeper {__version__}")


@app.command()
def doctor() -> None:
    """Run end-to-end health checks (placeholder — implemented in Phase 0.5)."""
    console.print("[yellow]doctor: not implemented yet (Phase 0.5).[/yellow]")
    console.print(f"python : {sys.version.split()[0]}")
    console.print(f"system : {platform.system()} {platform.machine()}")
    raise typer.Exit(code=0)


if __name__ == "__main__":  # pragma: no cover
    app()
