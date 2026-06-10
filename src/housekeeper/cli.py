"""Housekeeper CLI entry point.

The CLI is the single user-facing surface for ops tasks: ``housekeeper doctor``
(health checks), ``housekeeper tail`` (event stream), ``housekeeper models ...``
(model registry helpers), etc. Subcommands are added in later phases.
"""

from __future__ import annotations

import platform
import sys

import typer
from rich.console import Console
from rich.table import Table

from housekeeper import __version__, models

app = typer.Typer(
    name="housekeeper",
    help="Local-first AI agent that watches an IP camera and talks to you.",
    no_args_is_help=True,
    add_completion=False,
)

# Subcommand group: ``housekeeper models ...``
models_app = typer.Typer(
    name="models",
    help="Inspect and verify the local model registry (configs/models.yaml).",
    no_args_is_help=True,
)
app.add_typer(models_app)

console = Console()


@app.command()
def version() -> None:
    """Print the Housekeeper version."""
    console.print(f"housekeeper {__version__}")


@app.command()
def doctor() -> None:
    """Run end-to-end health checks (placeholder — implemented in Phase 0.6)."""
    console.print("[yellow]doctor: not implemented yet (Phase 0.6).[/yellow]")
    console.print(f"python : {sys.version.split()[0]}")
    console.print(f"system : {platform.system()} {platform.machine()}")
    raise typer.Exit(code=0)


# ---------------------------------------------------------------------------
# models subcommand group
# ---------------------------------------------------------------------------


def _profile_option() -> str:
    """Used as the Typer default for --profile."""
    return models.default_profile()


@models_app.command("list")
def models_list() -> None:
    """Show every model in configs/models.yaml and the profiles they belong to."""
    config = models.load_config()

    table = Table(title="Model registry", show_lines=False)
    table.add_column("key", style="cyan")
    table.add_column("backend")
    table.add_column("name")
    table.add_column("~GB", justify="right")
    table.add_column("role")
    for key, spec in config.models.items():
        table.add_row(key, spec.backend, spec.name, f"{spec.approx_gb:.1f}", spec.role)
    console.print(table)

    console.print("\n[bold]Profiles[/bold]")
    for name, members in config.profiles.items():
        marker = "  (default for this host)" if name == models.default_profile() else ""
        console.print(f"  [cyan]{name}[/cyan]: {', '.join(members)}{marker}")


@models_app.command("verify")
def models_verify(
    profile: str = typer.Option(
        None,
        "--profile",
        "-p",
        help="Profile to verify. Defaults to 'standard' on Apple Silicon, 'minimal' elsewhere.",
    ),
) -> None:
    """Check whether every model in a profile is available locally."""
    config = models.load_config()
    chosen = profile or models.default_profile()
    try:
        results = models.verify_profile(config, chosen)
    except KeyError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=2) from exc

    table = Table(title=f"Profile: {chosen}", show_lines=False)
    table.add_column("key", style="cyan")
    table.add_column("backend")
    table.add_column("name")
    table.add_column("status")
    table.add_column("detail", overflow="fold")

    status_style = {
        models.Status.AVAILABLE: "[green]available[/green]",
        models.Status.MISSING: "[yellow]missing[/yellow]",
        models.Status.UNREACHABLE: "[red]unreachable[/red]",
        models.Status.SKIPPED: "[dim]skipped[/dim]",
        models.Status.ERROR: "[red]error[/red]",
    }
    for r in results:
        table.add_row(
            r.key,
            r.spec.backend,
            r.spec.name,
            status_style[r.status],
            r.detail,
        )
    console.print(table)

    failed = [r for r in results if not r.ok]
    if failed:
        console.print(
            f"\n[red]{len(failed)} model(s) not ready.[/red] "
            "Run ./scripts/bootstrap_models.sh to pull missing models."
        )
        raise typer.Exit(code=1)
    console.print("\n[green]All models in profile are ready.[/green]")


if __name__ == "__main__":  # pragma: no cover
    app()
