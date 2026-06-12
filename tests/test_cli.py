"""Tests for the Typer CLI entry point."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from housekeeper import __version__, bus, models, notifier, services
from housekeeper.cli import app

runner = CliRunner()


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    # Typer's ``no_args_is_help`` prints help; exit code is 2 (usage error).
    assert result.exit_code == 2
    assert "usage" in result.stdout.lower()
    assert "housekeeper" in result.stdout.lower()


def test_help_flag_exits_zero() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "usage" in result.stdout.lower()


def test_version_command_prints_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_doctor_placeholder_exits_zero() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "not implemented" in result.stdout.lower()


def test_unknown_command_exits_nonzero() -> None:
    result = runner.invoke(app, ["definitely-not-a-real-command"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# ``housekeeper models`` subcommand group
# ---------------------------------------------------------------------------


def test_models_list_prints_registry() -> None:
    result = runner.invoke(app, ["models", "list"])
    assert result.exit_code == 0
    # Every registered key from the real config should appear.
    cfg = models.load_config()
    for key in cfg.models:
        assert key in result.stdout


def test_models_verify_reports_status(monkeypatch: pytest.MonkeyPatch) -> None:
    # Pretend Ollama returned an empty model list — every Ollama model
    # should come back as missing.
    monkeypatch.setattr(models, "_list_ollama_models", lambda endpoint, timeout=2.0: [])
    result = runner.invoke(app, ["models", "verify", "--profile", "minimal"])
    # Missing models → non-zero exit.
    assert result.exit_code == 1
    assert "missing" in result.stdout.lower()


def test_models_verify_unknown_profile_exits_2() -> None:
    result = runner.invoke(app, ["models", "verify", "--profile", "ghost"])
    assert result.exit_code == 2
    assert "unknown profile" in result.stdout.lower()


# ---------------------------------------------------------------------------
# ``housekeeper notify`` subcommand group
# ---------------------------------------------------------------------------


def _patch_services_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Redirect services config + override paths into a tmp dir."""
    local = tmp_path / "services.yaml.local"
    monkeypatch.setattr(services, "LOCAL_OVERRIDE_PATH", local)
    # Point the default loader at a fixed base so test isolation is clean.
    base = tmp_path / "services.yaml"
    base.write_text(
        yaml.safe_dump(
            {
                "ntfy": {
                    "endpoint": "http://127.0.0.1:8080",
                    "topic": "default-topic",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(services, "DEFAULT_CONFIG_PATH", base)
    return local


def test_notify_init_creates_local_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = _patch_services_paths(monkeypatch, tmp_path)
    result = runner.invoke(app, ["notify", "init"])
    assert result.exit_code == 0, result.stdout
    assert local.exists()
    payload = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert payload["ntfy"]["topic"].startswith("housekeeper-")
    assert "wrote" in result.stdout.lower()


def test_notify_init_refuses_to_overwrite_without_force(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = _patch_services_paths(monkeypatch, tmp_path)
    local.write_text("ntfy: {topic: existing}\n", encoding="utf-8")
    result = runner.invoke(app, ["notify", "init"])
    assert result.exit_code == 1
    assert "refusing" in result.stdout.lower()


def test_notify_init_force_overwrites(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local = _patch_services_paths(monkeypatch, tmp_path)
    local.write_text("ntfy: {topic: existing}\n", encoding="utf-8")
    result = runner.invoke(app, ["notify", "init", "--force"])
    assert result.exit_code == 0
    payload = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert payload["ntfy"]["topic"] != "existing"


def test_notify_show_prints_resolved_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_services_paths(monkeypatch, tmp_path)
    result = runner.invoke(app, ["notify", "show"])
    assert result.exit_code == 0
    assert "default-topic" in result.stdout
    assert "http://127.0.0.1:8080" in result.stdout


def test_notify_send_publishes_and_reports_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_services_paths(monkeypatch, tmp_path)

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["title"] = request.headers.get("title")
        captured["priority"] = request.headers.get("priority")
        return httpx.Response(200, json={"id": "x"})

    fake_client = httpx.Client(transport=httpx.MockTransport(handler))

    class _StubNotifier(notifier.NtfyNotifier):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(client=fake_client)

    monkeypatch.setattr(notifier, "NtfyNotifier", _StubNotifier)

    result = runner.invoke(
        app,
        ["notify", "send", "Hello", "world", "--priority", "4", "--tags", "a,b"],
    )
    assert result.exit_code == 0, result.stdout
    assert "sent" in result.stdout.lower()
    assert captured["url"].endswith("/default-topic")
    assert captured["title"] == "Hello"
    assert captured["priority"] == "4"


def test_notify_send_reports_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _patch_services_paths(monkeypatch, tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake_client = httpx.Client(transport=httpx.MockTransport(handler))

    class _StubNotifier(notifier.NtfyNotifier):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(client=fake_client)

    monkeypatch.setattr(notifier, "NtfyNotifier", _StubNotifier)

    result = runner.invoke(app, ["notify", "send", "t", "b"])
    assert result.exit_code == 1
    assert "failed" in result.stdout.lower()


def test_notify_verify_returns_zero_on_reachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_services_paths(monkeypatch, tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    fake_client = httpx.Client(transport=httpx.MockTransport(handler))

    class _StubNotifier(notifier.NtfyNotifier):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(client=fake_client)

    monkeypatch.setattr(notifier, "NtfyNotifier", _StubNotifier)

    result = runner.invoke(app, ["notify", "verify"])
    assert result.exit_code == 0
    assert "reachable" in result.stdout.lower()


def test_notify_verify_returns_one_on_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_services_paths(monkeypatch, tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    fake_client = httpx.Client(transport=httpx.MockTransport(handler))

    class _StubNotifier(notifier.NtfyNotifier):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(client=fake_client)

    monkeypatch.setattr(notifier, "NtfyNotifier", _StubNotifier)

    result = runner.invoke(app, ["notify", "verify"])
    assert result.exit_code == 1
    assert "unreachable" in result.stdout.lower()


# ---------------------------------------------------------------------------
# ``housekeeper bus`` subcommand group
# ---------------------------------------------------------------------------


@dataclass
class _BusStub:
    """Records every Bus method the CLI calls, no NATS required."""

    verify_result: bus.BusVerifyResult | None = None
    ensure_result: tuple[bool, Any] | None = None
    info_result: dict[str, Any] | None = None
    published: list[tuple[str, bytes]] | None = None
    verify_raises: Exception | None = None
    ensure_raises: Exception | None = None
    info_raises: Exception | None = None
    publish_raises: Exception | None = None

    async def __aenter__(self) -> _BusStub:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        pass

    async def verify(self) -> bus.BusVerifyResult:
        if self.verify_raises:
            raise self.verify_raises
        assert self.verify_result is not None
        return self.verify_result

    async def ensure_stream(self) -> tuple[bool, Any]:
        if self.ensure_raises:
            raise self.ensure_raises
        assert self.ensure_result is not None
        return self.ensure_result

    async def stream_info(self) -> dict[str, Any]:
        if self.info_raises:
            raise self.info_raises
        assert self.info_result is not None
        return self.info_result

    async def publish(
        self, subject: str, payload: bytes, *, headers: dict[str, str] | None = None
    ) -> None:
        if self.publish_raises:
            raise self.publish_raises
        if self.published is not None:
            self.published.append((subject, payload))


def _install_bus_stub(monkeypatch: pytest.MonkeyPatch, stub: _BusStub) -> None:
    def _factory(*_args: object, **_kwargs: object) -> _BusStub:
        return stub

    monkeypatch.setattr(bus, "Bus", _factory)


def test_bus_verify_zero_on_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _BusStub(
        verify_result=bus.BusVerifyResult(
            bus.BusStatus.REACHABLE, "nats://127.0.0.1:4222", "rtt=1.0ms"
        )
    )
    _install_bus_stub(monkeypatch, stub)
    result = runner.invoke(app, ["bus", "verify"])
    assert result.exit_code == 0
    assert "reachable" in result.stdout.lower()


def test_bus_verify_one_on_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _BusStub(
        verify_result=bus.BusVerifyResult(
            bus.BusStatus.UNREACHABLE, "nats://127.0.0.1:4222", "refused"
        )
    )
    _install_bus_stub(monkeypatch, stub)
    result = runner.invoke(app, ["bus", "verify"])
    assert result.exit_code == 1
    assert "unreachable" in result.stdout.lower()


def test_bus_init_reports_created(monkeypatch: pytest.MonkeyPatch) -> None:
    @dataclass
    class _Cfg:
        name: str = "HOUSEKEEPER_VIDEO"
        subjects: list[str] = None  # type: ignore[assignment]

    cfg = _Cfg(subjects=["video.events", "video.events.>"])
    stub = _BusStub(ensure_result=(True, cfg))
    _install_bus_stub(monkeypatch, stub)
    result = runner.invoke(app, ["bus", "init"])
    assert result.exit_code == 0
    assert "created" in result.stdout.lower()
    assert "HOUSEKEEPER_VIDEO" in result.stdout


def test_bus_init_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _BusStub(ensure_raises=OSError("connection refused"))
    _install_bus_stub(monkeypatch, stub)
    result = runner.invoke(app, ["bus", "init"])
    assert result.exit_code == 1
    assert "failed" in result.stdout.lower()


def test_bus_info_renders_table(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _BusStub(
        info_result={
            "name": "HOUSEKEEPER_VIDEO",
            "subjects": ["video.events"],
            "messages": 3,
            "bytes": 1024,
            "first_seq": 1,
            "last_seq": 3,
        }
    )
    _install_bus_stub(monkeypatch, stub)
    result = runner.invoke(app, ["bus", "info"])
    assert result.exit_code == 0
    assert "HOUSEKEEPER_VIDEO" in result.stdout
    assert "messages" in result.stdout


def test_bus_publish_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sink: list[tuple[str, bytes]] = []
    stub = _BusStub(published=sink)
    _install_bus_stub(monkeypatch, stub)
    result = runner.invoke(app, ["bus", "publish", "video.events.test", "hello"])
    assert result.exit_code == 0
    assert sink == [("video.events.test", b"hello")]
    assert "published" in result.stdout.lower()


def test_bus_publish_failure_exits_one(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _BusStub(publish_raises=OSError("refused"))
    _install_bus_stub(monkeypatch, stub)
    result = runner.invoke(app, ["bus", "publish", "x", "y"])
    assert result.exit_code == 1
    assert "failed" in result.stdout.lower()
