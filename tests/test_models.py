"""Tests for ``housekeeper.models``."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import yaml

from housekeeper import models, platform_info

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


SAMPLE_CONFIG = {
    "endpoints": {"ollama": "http://127.0.0.1:11434"},
    "models": {
        "vlm_fast": {
            "backend": "ollama",
            "name": "moondream",
            "role": "Tier-1 VLM",
            "approx_gb": 1.7,
        },
        "embed": {
            "backend": "ollama",
            "name": "nomic-embed-text",
            "role": "Embeddings",
            "approx_gb": 0.3,
        },
        "vlm_smart": {
            "backend": "mlx",
            "name": "mlx-community/Qwen2.5-VL-7B-Instruct-4bit",
            "role": "Tier-2 VLM",
            "approx_gb": 6.0,
        },
    },
    "profiles": {
        "minimal": ["vlm_fast", "embed"],
        "full": ["vlm_fast", "embed", "vlm_smart"],
    },
}


@pytest.fixture
def sample_config_path(tmp_path: Path) -> Path:
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(SAMPLE_CONFIG), encoding="utf-8")
    return path


@pytest.fixture
def sample_config(sample_config_path: Path) -> models.ModelsConfig:
    return models.load_config(sample_config_path)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_config_parses_models_and_profiles(
    sample_config: models.ModelsConfig,
) -> None:
    assert set(sample_config.models) == {"vlm_fast", "embed", "vlm_smart"}
    assert sample_config.profiles["full"] == ["vlm_fast", "embed", "vlm_smart"]
    assert sample_config.endpoints.ollama.startswith("http")


def test_load_config_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        models.load_config(tmp_path / "nope.yaml")


def test_profile_references_unknown_model_raises(tmp_path: Path) -> None:
    bad = dict(SAMPLE_CONFIG)
    bad = {**bad, "profiles": {"broken": ["does_not_exist"]}}
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017  pydantic ValidationError
        models.load_config(path)


def test_default_real_config_loads() -> None:
    cfg = models.load_config()
    assert "vlm_fast" in cfg.models
    assert "minimal" in cfg.profiles


# ---------------------------------------------------------------------------
# Profile resolution
# ---------------------------------------------------------------------------


def test_resolve_profile_returns_specs_in_order(
    sample_config: models.ModelsConfig,
) -> None:
    members = models.resolve_profile(sample_config, "full")
    assert [k for k, _ in members] == ["vlm_fast", "embed", "vlm_smart"]


def test_resolve_unknown_profile_raises(sample_config: models.ModelsConfig) -> None:
    with pytest.raises(KeyError):
        models.resolve_profile(sample_config, "ghost")


def test_default_profile_depends_on_platform() -> None:
    assert models.default_profile() in {"minimal", "standard"}
    if platform_info.is_apple_silicon():
        assert models.default_profile() == "standard"
    else:
        assert models.default_profile() == "minimal"


# ---------------------------------------------------------------------------
# verify_one — Ollama branch
# ---------------------------------------------------------------------------


def _fake_lister(installed: list[str]):  # type: ignore[no-untyped-def]
    def _impl(endpoint: str, *, timeout: float = 2.0) -> list[str]:
        del endpoint, timeout
        return installed

    return _impl


def test_verify_ollama_available_exact_tag(sample_config: models.ModelsConfig) -> None:
    spec = sample_config.models["vlm_fast"]
    result = models.verify_one(
        "vlm_fast",
        spec,
        ollama_endpoint="http://x",
        ollama_lister=_fake_lister(["moondream:latest"]),
    )
    assert result.status is models.Status.AVAILABLE
    assert result.ok


def test_verify_ollama_missing(sample_config: models.ModelsConfig) -> None:
    spec = sample_config.models["vlm_fast"]
    result = models.verify_one(
        "vlm_fast",
        spec,
        ollama_endpoint="http://x",
        ollama_lister=_fake_lister(["qwen2.5:7b-instruct"]),
    )
    assert result.status is models.Status.MISSING
    assert not result.ok
    assert "ollama pull moondream" in result.detail


def test_verify_ollama_unreachable(sample_config: models.ModelsConfig) -> None:
    spec = sample_config.models["vlm_fast"]

    def boom(_endpoint: str, *, timeout: float = 2.0) -> list[str]:
        raise httpx.ConnectError("connection refused")

    result = models.verify_one(
        "vlm_fast",
        spec,
        ollama_endpoint="http://x",
        ollama_lister=boom,
    )
    assert result.status is models.Status.UNREACHABLE


# ---------------------------------------------------------------------------
# verify_one — MLX branch (platform-dependent)
# ---------------------------------------------------------------------------


def test_verify_mlx_skipped_on_non_apple_silicon(
    sample_config: models.ModelsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models.platform_info, "supports_mlx", lambda: False)
    spec = sample_config.models["vlm_smart"]
    result = models.verify_one(
        "vlm_smart",
        spec,
        ollama_endpoint="http://x",
        ollama_lister=_fake_lister([]),
    )
    assert result.status is models.Status.SKIPPED
    assert result.ok  # skipped is acceptable for irrelevant backends


def test_verify_mlx_deferred_on_apple_silicon(
    sample_config: models.ModelsConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models.platform_info, "supports_mlx", lambda: True)
    spec = sample_config.models["vlm_smart"]
    result = models.verify_one(
        "vlm_smart",
        spec,
        ollama_endpoint="http://x",
        ollama_lister=_fake_lister([]),
    )
    assert result.status is models.Status.MISSING
    assert "Phase 6" in result.detail


# ---------------------------------------------------------------------------
# Name matching edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("installed", "wanted", "expected"),
    [
        (["moondream:latest"], "moondream", True),
        (["moondream:v2"], "moondream", True),
        (["moondream:latest"], "moondream:v2", False),
        (["qwen2.5:7b-instruct"], "qwen2.5:7b-instruct", True),
        ([], "anything", False),
    ],
)
def test_ollama_name_match(installed: list[str], wanted: str, expected: bool) -> None:
    assert models._ollama_name_matches(installed, wanted) is expected
