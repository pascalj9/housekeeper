"""Tests for ``housekeeper.platform_info``."""

from __future__ import annotations

import platform

from housekeeper import platform_info


def test_is_macos_matches_platform() -> None:
    assert platform_info.is_macos() == (platform.system() == "Darwin")


def test_is_linux_matches_platform() -> None:
    assert platform_info.is_linux() == (platform.system() == "Linux")


def test_apple_silicon_implies_macos() -> None:
    if platform_info.is_apple_silicon():
        assert platform_info.is_macos()


def test_wsl_implies_linux() -> None:
    if platform_info.is_wsl():
        assert platform_info.is_linux()


def test_supports_mlx_only_on_apple_silicon() -> None:
    assert platform_info.supports_mlx() == platform_info.is_apple_silicon()


def test_predicates_return_bool() -> None:
    for fn in (
        platform_info.is_macos,
        platform_info.is_apple_silicon,
        platform_info.is_linux,
        platform_info.is_wsl,
        platform_info.supports_mlx,
    ):
        assert isinstance(fn(), bool)
