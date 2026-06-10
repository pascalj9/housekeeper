"""Cross-platform helpers.

Housekeeper is developed on Linux/WSL and deployed on macOS (Apple Silicon).
This module centralises the few places where the two diverge so the rest of
the codebase can stay platform-agnostic.
"""

from __future__ import annotations

import platform


def is_macos() -> bool:
    """Return True when running on macOS."""
    return platform.system() == "Darwin"


def is_apple_silicon() -> bool:
    """Return True when running on Apple Silicon (arm64 macOS)."""
    return is_macos() and platform.machine() == "arm64"


def is_linux() -> bool:
    """Return True when running on Linux (includes WSL)."""
    return platform.system() == "Linux"


def is_wsl() -> bool:
    """Return True when running inside WSL.

    Detection uses the canonical ``microsoft`` marker in ``/proc/version``;
    works for both WSL1 and WSL2.
    """
    if not is_linux():
        return False
    try:
        with open("/proc/version", encoding="utf-8") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def supports_mlx() -> bool:
    """Return True when the MLX inference stack can be used on this host."""
    return is_apple_silicon()
