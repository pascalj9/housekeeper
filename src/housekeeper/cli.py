"""Housekeeper CLI entry point.

The CLI is the single user-facing surface for ops tasks: ``housekeeper doctor``
(health checks), ``housekeeper tail`` (event stream), ``housekeeper models ...``
(model registry helpers), etc. Subcommands are added in later phases.
"""

from __future__ import annotations

import asyncio
import platform
import secrets
import sys

import httpx
import typer
import yaml
from rich.console import Console
from rich.table import Table

from housekeeper import __version__, bus, models, notifier, services

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

# Subcommand group: ``housekeeper notify ...``
notify_app = typer.Typer(
    name="notify",
    help="Send / verify notifications via the local ntfy server.",
    no_args_is_help=True,
)
app.add_typer(notify_app)

# Subcommand group: ``housekeeper bus ...``
bus_app = typer.Typer(
    name="bus",
    help="Interact with the local NATS event bus.",
    no_args_is_help=True,
)
app.add_typer(bus_app)

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


# ---------------------------------------------------------------------------
# notify subcommand group
# ---------------------------------------------------------------------------


@notify_app.command("init")
def notify_init(
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite an existing services.yaml.local."
    ),
) -> None:
    """Generate a random ntfy topic and write it to services.yaml.local."""
    local_path = services.LOCAL_OVERRIDE_PATH
    if local_path.exists() and not force:
        console.print(f"[yellow]Refusing to overwrite[/yellow] {local_path} (use --force).")
        raise typer.Exit(code=1)

    topic = f"housekeeper-{secrets.token_urlsafe(12)}"
    payload = {"ntfy": {"topic": topic}}
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with local_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)

    cfg = services.load_config()
    console.print(f"[green]Wrote[/green] {local_path}")
    console.print(f"  topic       : [cyan]{cfg.ntfy.topic}[/cyan]")
    console.print(f"  publish URL : {cfg.ntfy.topic_url}")
    console.print("\nSubscribe from the ntfy phone app using the publish URL above.")


@notify_app.command("show")
def notify_show() -> None:
    """Print the resolved ntfy config (after merging defaults + local override)."""
    cfg = services.load_config().ntfy
    console.print(f"endpoint    : {cfg.endpoint}")
    console.print(f"topic       : {cfg.topic}")
    console.print(f"publish URL : {cfg.topic_url}")
    console.print(f"auth_token  : {'<set>' if cfg.auth_token else '<none>'}")


@notify_app.command("send")
def notify_send(
    title: str = typer.Argument(..., help="Notification title."),
    body: str = typer.Argument(..., help="Notification body."),
    priority: notifier.Priority = typer.Option(
        notifier.Priority.DEFAULT,
        "--priority",
        "-p",
        help="ntfy priority (1=min, 5=urgent).",
    ),
    tags: str = typer.Option(
        "", "--tags", "-t", help="Comma-separated tag list (e.g. 'house,camera')."
    ),
    click_url: str = typer.Option(
        "", "--click", "-c", help="Optional URL to open when the user taps the push."
    ),
) -> None:
    """Send one ad-hoc notification (useful as a smoke test)."""
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    with notifier.NtfyNotifier() as notif:
        try:
            resp = notif.send(
                title=title,
                body=body,
                priority=priority,
                tags=tag_list,
                click_url=click_url or None,
            )
        except httpx.HTTPError as exc:
            console.print(f"[red]ntfy send failed:[/red] {exc}")
            raise typer.Exit(code=1) from exc
    console.print(f"[green]sent[/green] (HTTP {resp.status_code}) → {notif.config.topic_url}")


@notify_app.command("verify")
def notify_verify() -> None:
    """Probe the configured ntfy server. Returns 0 if reachable, 1 otherwise."""
    with notifier.NtfyNotifier() as notif:
        result = notif.verify()

    if result.ok:
        console.print(f"[green]{result.status}[/green] {result.endpoint} ({result.detail})")
    else:
        console.print(f"[red]{result.status}[/red] {result.endpoint} — {result.detail}")
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# bus subcommand group
# ---------------------------------------------------------------------------


@bus_app.command("verify")
def bus_verify() -> None:
    """Probe the local NATS server. Returns 0 if reachable, 1 otherwise."""

    async def _run() -> bus.BusVerifyResult:
        return await bus.Bus().verify()

    result = asyncio.run(_run())
    if result.ok:
        console.print(f"[green]{result.status}[/green] {result.url} ({result.detail})")
    else:
        console.print(f"[red]{result.status}[/red] {result.url} — {result.detail}")
        raise typer.Exit(code=1)


@bus_app.command("init")
def bus_init() -> None:
    """Create or update the JetStream stream that holds video.events."""

    async def _run() -> tuple[bool, str, list[str]]:
        async with bus.Bus() as b:
            created, cfg = await b.ensure_stream()
        return created, cfg.name, list(cfg.subjects or [])

    try:
        created, name, subjects = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]bus init failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    verb = "[green]created[/green]" if created else "[cyan]updated[/cyan]"
    console.print(f"{verb} stream [bold]{name}[/bold]")
    console.print(f"  subjects: {', '.join(subjects)}")


@bus_app.command("info")
def bus_info() -> None:
    """Show JetStream stream state (subjects, message count, bytes)."""

    async def _run() -> dict[str, object]:
        async with bus.Bus() as b:
            return await b.stream_info()

    try:
        info = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]bus info failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    table = Table(title=f"JetStream: {info['name']}", show_lines=False)
    table.add_column("field", style="cyan")
    table.add_column("value")
    for key in ("subjects", "messages", "bytes", "first_seq", "last_seq"):
        table.add_row(key, str(info[key]))
    console.print(table)


@bus_app.command("publish")
def bus_publish(
    subject: str = typer.Argument(..., help="NATS subject, e.g. video.events.test"),
    payload: str = typer.Argument(..., help="Message body (UTF-8 string)."),
) -> None:
    """Publish a one-off message (smoke test)."""

    async def _run() -> None:
        async with bus.Bus() as b:
            await b.publish(subject, payload.encode("utf-8"))

    try:
        asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]publish failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]published[/green] {len(payload)}B → {subject}")


@bus_app.command("sub")
def bus_sub(
    subject: str = typer.Argument(..., help="NATS subject or wildcard, e.g. video.events.>"),
    count: int = typer.Option(
        0, "--count", "-n", help="Stop after N messages (0 = run until Ctrl-C)."
    ),
) -> None:
    """Subscribe to a subject and print messages until Ctrl-C or --count hit."""

    async def _run() -> int:
        received = 0
        async with bus.Bus() as b:
            async for msg in b.subscribe_iter(subject):
                received += 1
                console.print(
                    f"[dim]#{received:04d}[/dim] [cyan]{msg.subject}[/cyan] "
                    f"{msg.data.decode('utf-8', errors='replace')}"
                )
                if count and received >= count:
                    break
        return received

    try:
        n = asyncio.run(_run())
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        raise typer.Exit(code=0) from None
    except Exception as exc:
        console.print(f"[red]subscribe failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]done[/green] ({n} message(s) received)")


if __name__ == "__main__":  # pragma: no cover
    app()
