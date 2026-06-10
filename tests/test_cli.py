"""Tests for the Typer CLI entry point."""

from __future__ import annotations

from typer.testing import CliRunner

from housekeeper import __version__
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
