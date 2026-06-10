"""Smoke tests for the top-level ``housekeeper`` package."""

from __future__ import annotations

import re

import housekeeper


def test_version_is_string() -> None:
    assert isinstance(housekeeper.__version__, str)


def test_version_matches_semver_like() -> None:
    # Loose semver: N.N.N optionally followed by -tag.
    assert re.match(r"^\d+\.\d+\.\d+(?:[-.][\w.]+)?$", housekeeper.__version__)
