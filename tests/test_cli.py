"""Tests for the Typer CLI entry point."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from housekeeper import __version__, models
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
