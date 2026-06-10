"""Model registry and availability checks.

This module is the **only** place that knows how to read
``configs/models.yaml`` and probe local inference backends (Ollama today, MLX
in Phase 6). Both the bootstrap script (via ``housekeeper models ...``
subcommands) and the future ``housekeeper doctor`` rely on it.

Design notes
------------
* The config schema is validated with Pydantic so a bad YAML fails loudly at
  load time instead of producing confusing errors later.
* Verification never *pulls* a model; it only checks whether it is already
  available. The bash bootstrap script is responsible for pulling.
* Anything that touches the network or filesystem is wrapped so it can be
  monkey-patched in unit tests; integration tests that exercise a real Ollama
  are gated behind the ``integration`` pytest marker.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

import httpx
import yaml
from pydantic import BaseModel, Field, field_validator

from housekeeper import platform_info

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class ModelSpec(BaseModel):
    """One row of ``configs/models.yaml``'s ``models:`` section."""

    backend: Literal["ollama", "mlx"]
    name: str
    role: str = ""
    approx_gb: float = Field(default=0.0, ge=0.0)


class Endpoints(BaseModel):
    ollama: str = "http://127.0.0.1:11434"


class ModelsConfig(BaseModel):
    endpoints: Endpoints = Field(default_factory=Endpoints)
    models: dict[str, ModelSpec]
    profiles: dict[str, list[str]]

    @field_validator("profiles")
    @classmethod
    def _profile_keys_must_exist(
        cls,
        profiles: dict[str, list[str]],
        info,  # type: ignore[no-untyped-def]
    ) -> dict[str, list[str]]:
        model_keys = set((info.data.get("models") or {}).keys())
        for profile, members in profiles.items():
            unknown = sorted(set(members) - model_keys)
            if unknown:
                raise ValueError(f"profile '{profile}' references unknown model keys: {unknown}")
        return profiles


# ---------------------------------------------------------------------------
# Verification result
# ---------------------------------------------------------------------------


class Status(StrEnum):
    """Outcome of a single model availability check."""

    AVAILABLE = "available"
    MISSING = "missing"
    UNREACHABLE = "unreachable"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True)
class VerifyResult:
    key: str
    spec: ModelSpec
    status: Status
    detail: str = ""

    @property
    def ok(self) -> bool:
        # SKIPPED is considered OK because the model isn't applicable here.
        return self.status in (Status.AVAILABLE, Status.SKIPPED)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "models.yaml"


def load_config(path: Path | str | None = None) -> ModelsConfig:
    """Load and validate ``configs/models.yaml`` (or the path given)."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Model config not found: {config_path}")
    with config_path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return ModelsConfig.model_validate(raw)


def resolve_profile(config: ModelsConfig, profile: str) -> list[tuple[str, ModelSpec]]:
    """Return the (key, spec) pairs that make up ``profile``."""
    if profile not in config.profiles:
        known = ", ".join(sorted(config.profiles))
        raise KeyError(f"Unknown profile '{profile}'. Known profiles: {known}")
    return [(key, config.models[key]) for key in config.profiles[profile]]


def default_profile() -> str:
    """Pick a sensible default profile for this host.

    Apple Silicon Macs default to ``standard``; everything else (including
    WSL) defaults to ``minimal`` so dev boxes don't get clobbered by 14 GB of
    weights they can't usefully run anyway.
    """
    return "standard" if platform_info.is_apple_silicon() else "minimal"


# ---------------------------------------------------------------------------
# Backend probes
# ---------------------------------------------------------------------------


def _list_ollama_models(endpoint: str, *, timeout: float = 2.0) -> list[str]:
    """Return the list of locally-installed Ollama model names.

    Raises ``httpx.HTTPError`` if Ollama is unreachable.
    """
    resp = httpx.get(f"{endpoint.rstrip('/')}/api/tags", timeout=timeout)
    resp.raise_for_status()
    payload = resp.json() or {}
    return [m.get("name", "") for m in payload.get("models", [])]


def _ollama_name_matches(installed: list[str], wanted: str) -> bool:
    """Match an Ollama spec name against the installed list.

    Ollama tags everything as ``name:tag``. ``moondream`` is shorthand for
    ``moondream:latest``; we treat the bare name as a match against any tag.
    """
    if ":" in wanted:
        return wanted in installed
    return any(m.split(":", 1)[0] == wanted for m in installed)


def verify_one(
    key: str,
    spec: ModelSpec,
    *,
    ollama_endpoint: str,
    ollama_lister=None,  # type: ignore[no-untyped-def]
) -> VerifyResult:
    """Check whether a single model is available on this host.

    The ``ollama_lister`` argument exists for unit tests; when omitted, the
    module-level ``_list_ollama_models`` is resolved at call time so that
    tests can ``monkeypatch.setattr(models, "_list_ollama_models", ...)``.
    """
    if ollama_lister is None:
        ollama_lister = _list_ollama_models
    if spec.backend == "ollama":
        try:
            installed = ollama_lister(ollama_endpoint)
        except httpx.HTTPError as exc:
            return VerifyResult(key, spec, Status.UNREACHABLE, f"ollama: {exc}")
        if _ollama_name_matches(installed, spec.name):
            return VerifyResult(key, spec, Status.AVAILABLE, spec.name)
        return VerifyResult(key, spec, Status.MISSING, f"not pulled: ollama pull {spec.name}")

    if spec.backend == "mlx":
        if not platform_info.supports_mlx():
            return VerifyResult(key, spec, Status.SKIPPED, "MLX requires Apple Silicon")
        # Phase 6 will wire the real MLX cache check. For now report missing so
        # the user knows there is still work to do.
        return VerifyResult(key, spec, Status.MISSING, "MLX bootstrap deferred to Phase 6")

    return VerifyResult(key, spec, Status.ERROR, f"unknown backend: {spec.backend}")


def verify_profile(config: ModelsConfig, profile: str) -> list[VerifyResult]:
    """Verify every model in ``profile`` and return per-model results."""
    return [
        verify_one(key, spec, ollama_endpoint=config.endpoints.ollama)
        for key, spec in resolve_profile(config, profile)
    ]
