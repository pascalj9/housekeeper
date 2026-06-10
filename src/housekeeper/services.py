"""Service registry loader (ntfy today, NATS in Phase 0.4).

The committed defaults live in ``configs/services.yaml``. Per-host overrides
go in ``configs/services.yaml.local`` (gitignored) — typically the actual
ntfy topic. Both files are loaded and shallow-merged.

Design notes
------------
* Merge strategy is shallow per top-level key (``ntfy``, future ``nats``).
  Within a section, ``.local`` values fully override defaults — no deep merge.
  This keeps surprises minimal and matches how most app configs behave.
* All paths are resolved relative to the repo root so the same code runs in
  ``uv run`` from any cwd.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "services.yaml"
LOCAL_OVERRIDE_PATH = REPO_ROOT / "configs" / "services.yaml.local"


class NtfyConfig(BaseModel):
    endpoint: str = "http://127.0.0.1:8080"
    topic: str = "housekeeper-default"
    auth_token: str | None = None

    @property
    def topic_url(self) -> str:
        """Full publish URL: ``<endpoint>/<topic>``."""
        return f"{self.endpoint.rstrip('/')}/{self.topic}"


class ServicesConfig(BaseModel):
    ntfy: NtfyConfig = Field(default_factory=NtfyConfig)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _shallow_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge: per top-level key, override.value replaces base.value."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            sub = dict(merged[key])
            sub.update(value)
            merged[key] = sub
        else:
            merged[key] = value
    return merged


def load_config(
    base_path: Path | str | None = None,
    local_path: Path | str | None = None,
) -> ServicesConfig:
    """Load ``services.yaml`` (and optional ``services.yaml.local``) merged."""
    base = _read_yaml(Path(base_path) if base_path else DEFAULT_CONFIG_PATH)
    local = _read_yaml(Path(local_path) if local_path else LOCAL_OVERRIDE_PATH)
    merged = _shallow_merge(base, local)
    return ServicesConfig.model_validate(merged)
