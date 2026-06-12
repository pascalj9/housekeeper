"""Tests for ``housekeeper.services``."""

from __future__ import annotations

from pathlib import Path

import yaml

from housekeeper import services


def _write_yaml(path: Path, payload: object) -> None:
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_load_real_default_config_has_ntfy_section() -> None:
    cfg = services.load_config(local_path=Path("/nonexistent"))
    assert cfg.ntfy.endpoint.startswith("http")
    assert cfg.ntfy.topic


def test_topic_url_concatenates_endpoint_and_topic(tmp_path: Path) -> None:
    base = tmp_path / "services.yaml"
    _write_yaml(
        base,
        {"ntfy": {"endpoint": "http://10.0.0.1:8080/", "topic": "house-1234"}},
    )
    cfg = services.load_config(base, local_path=tmp_path / "missing.yaml")
    assert cfg.ntfy.topic_url == "http://10.0.0.1:8080/house-1234"


def test_local_override_replaces_defaults(tmp_path: Path) -> None:
    base = tmp_path / "services.yaml"
    local = tmp_path / "services.yaml.local"
    _write_yaml(
        base,
        {"ntfy": {"endpoint": "http://127.0.0.1:8080", "topic": "default"}},
    )
    _write_yaml(local, {"ntfy": {"topic": "secret-topic-xyz"}})

    cfg = services.load_config(base, local_path=local)
    # endpoint from defaults, topic from .local — sub-key shallow merge.
    assert cfg.ntfy.endpoint == "http://127.0.0.1:8080"
    assert cfg.ntfy.topic == "secret-topic-xyz"


def test_missing_base_and_local_yields_pydantic_defaults(tmp_path: Path) -> None:
    cfg = services.load_config(
        base_path=tmp_path / "nope.yaml",
        local_path=tmp_path / "also-nope.yaml",
    )
    # Defaults kick in.
    assert cfg.ntfy.endpoint == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------------
# NATS section (Phase 0.4)
# ---------------------------------------------------------------------------


def test_real_default_config_has_nats_section() -> None:
    cfg = services.load_config(local_path=Path("/nonexistent"))
    assert cfg.nats.url.startswith("nats://")
    assert cfg.nats.stream.name


def test_nats_section_round_trips_overrides(tmp_path: Path) -> None:
    base = tmp_path / "services.yaml"
    _write_yaml(
        base,
        {
            "nats": {
                "url": "nats://other:4222",
                "stream": {
                    "name": "ALT",
                    "subjects": ["x.>"],
                    "max_age_seconds": 60,
                    "max_bytes": 1024,
                },
            }
        },
    )
    cfg = services.load_config(base, local_path=tmp_path / "missing.yaml")
    assert cfg.nats.url == "nats://other:4222"
    assert cfg.nats.stream.name == "ALT"
    assert cfg.nats.stream.subjects == ["x.>"]
    assert cfg.nats.stream.max_age_seconds == 60
