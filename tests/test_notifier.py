"""Tests for ``housekeeper.notifier``."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from housekeeper.notifier import NtfyNotifier, NtfyStatus, Priority
from housekeeper.services import NtfyConfig


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    """Build an httpx client whose responses come from ``handler``."""
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def config() -> NtfyConfig:
    return NtfyConfig(
        endpoint="http://127.0.0.1:8080",
        topic="house-secret",
        auth_token=None,
    )


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------


def test_send_posts_to_topic_url_with_headers(config: NtfyConfig) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = request.content.decode("utf-8")
        return httpx.Response(200, json={"id": "msg-1"})

    with NtfyNotifier(config, client=_client(handler)) as notif:
        resp = notif.send(
            title="Hello",
            body="from house",
            priority=Priority.HIGH,
            tags=["camera", "porch"],
        )

    assert resp.status_code == 200
    assert captured["url"] == "http://127.0.0.1:8080/house-secret"
    headers = captured["headers"]
    assert headers["title"] == "Hello"
    assert headers["priority"] == "4"
    assert headers["tags"] == "camera,porch"
    assert "authorization" not in headers
    assert captured["body"] == "from house"


def test_send_includes_auth_header_when_token_set() -> None:
    cfg = NtfyConfig(
        endpoint="http://127.0.0.1:8080",
        topic="t",
        auth_token="abc123",
    )
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200)

    with NtfyNotifier(cfg, client=_client(handler)) as notif:
        notif.send(title="x", body="y")
    assert captured["authorization"] == "Bearer abc123"


def test_send_includes_click_url_when_provided(config: NtfyConfig) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200)

    with NtfyNotifier(config, client=_client(handler)) as notif:
        notif.send(title="t", body="b", click_url="https://example.com/x")
    assert captured["click"] == "https://example.com/x"


def test_send_raises_for_http_error(config: NtfyConfig) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with (
        NtfyNotifier(config, client=_client(handler)) as notif,
        pytest.raises(httpx.HTTPStatusError),
    ):
        notif.send(title="t", body="b")


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


def test_verify_returns_reachable_on_2xx(config: NtfyConfig) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ntfy")

    with NtfyNotifier(config, client=_client(handler)) as notif:
        result = notif.verify()
    assert result.status is NtfyStatus.REACHABLE
    assert result.ok


def test_verify_returns_unreachable_on_transport_error(
    config: NtfyConfig,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with NtfyNotifier(config, client=_client(handler)) as notif:
        result = notif.verify()
    assert result.status is NtfyStatus.UNREACHABLE
    assert not result.ok
    assert "refused" in result.detail


def test_verify_returns_error_on_5xx(config: NtfyConfig) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with NtfyNotifier(config, client=_client(handler)) as notif:
        result = notif.verify()
    assert result.status is NtfyStatus.ERROR
    assert not result.ok
