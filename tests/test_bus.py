"""Tests for ``housekeeper.bus``.

The wrapper is built around an injectable ``ConnectorFn``, so these tests use
a fake NATS client (just the methods the wrapper touches). Real-NATS round
trips are deferred to ``@pytest.mark.integration`` tests (Phase 0.7 doctor).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from housekeeper import bus
from housekeeper.services import NatsConfig, NatsStreamConfig

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class _FakeStreamInfo:
    config: Any
    state: Any


@dataclass
class _FakeState:
    messages: int = 0
    bytes: int = 0
    first_seq: int = 0
    last_seq: int = 0


@dataclass
class _FakeStreamConfig:
    name: str
    subjects: list[str] = field(default_factory=list)


@dataclass
class _FakeMsg:
    subject: str
    data: bytes


class _FakeSub:
    def __init__(self, messages: list[_FakeMsg]) -> None:
        self._messages = messages
        self.unsubscribed = False

    async def _gen(self) -> AsyncIterator[_FakeMsg]:
        for m in self._messages:
            yield m

    @property
    def messages(self) -> AsyncIterator[_FakeMsg]:
        return self._gen()

    async def unsubscribe(self) -> None:
        self.unsubscribed = True


class _FakeJS:
    def __init__(
        self,
        *,
        existing: _FakeStreamInfo | None = None,
        raise_not_found_first: bool = False,
    ) -> None:
        from nats.js.errors import NotFoundError  # local: only needed in tests

        self.NotFoundError = NotFoundError
        self.existing = existing
        self.raise_not_found_first = raise_not_found_first
        self.added: list[Any] = []
        self.updated: list[Any] = []

    async def stream_info(self, name: str) -> _FakeStreamInfo:
        if self.raise_not_found_first:
            self.raise_not_found_first = False
            raise self.NotFoundError
        if self.existing is None:
            raise self.NotFoundError
        return self.existing

    async def add_stream(self, *, config: Any) -> None:
        self.added.append(config)

    async def update_stream(self, *, config: Any) -> None:
        self.updated.append(config)


class _FakeNC:
    def __init__(self, *, js: _FakeJS, rtt_seconds: float = 0.001) -> None:
        self.is_connected = True
        self.published: list[tuple[str, bytes, dict[str, str] | None]] = []
        self._js = js
        self._rtt = rtt_seconds
        self.drained = False
        self.subs: list[_FakeSub] = []

    async def publish(
        self, subject: str, payload: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self.published.append((subject, payload, headers))

    async def subscribe(self, subject: str, queue: str = "") -> _FakeSub:
        sub = _FakeSub([_FakeMsg(subject=subject, data=b"hello")])
        self.subs.append(sub)
        return sub

    async def rtt(self) -> float:
        return self._rtt

    async def drain(self) -> None:
        self.drained = True
        self.is_connected = False

    def jetstream(self) -> _FakeJS:
        return self._js


def _make_bus(
    nc: _FakeNC | None = None,
    *,
    raise_on_connect: Exception | None = None,
) -> bus.Bus:
    js = _FakeJS()
    nc_obj = nc or _FakeNC(js=js)

    async def connector(_cfg: NatsConfig):  # type: ignore[no-untyped-def]
        if raise_on_connect is not None:
            raise raise_on_connect
        return nc_obj

    return bus.Bus(connector=connector)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_connect_then_close_drains() -> None:
    js = _FakeJS()
    nc = _FakeNC(js=js)
    b = _make_bus(nc)
    await b.connect()
    assert b.client is nc
    await b.close()
    assert nc.drained


async def test_context_manager_connects_and_closes() -> None:
    js = _FakeJS()
    nc = _FakeNC(js=js)
    b = _make_bus(nc)
    async with b:
        assert b.client is nc
    assert nc.drained


def test_client_before_connect_raises() -> None:
    b = _make_bus()
    with pytest.raises(RuntimeError):
        _ = b.client


# ---------------------------------------------------------------------------
# Publish / subscribe
# ---------------------------------------------------------------------------


async def test_publish_forwards_to_client() -> None:
    nc = _FakeNC(js=_FakeJS())
    b = _make_bus(nc)
    async with b:
        await b.publish("video.events.test", b"payload", headers={"x": "1"})
    assert nc.published == [("video.events.test", b"payload", {"x": "1"})]


# ---------------------------------------------------------------------------
# JetStream
# ---------------------------------------------------------------------------


async def test_ensure_stream_creates_when_missing() -> None:
    js = _FakeJS(existing=None)  # stream_info raises NotFoundError
    nc = _FakeNC(js=js)
    b = _make_bus(nc)
    async with b:
        created, cfg = await b.ensure_stream()
    assert created is True
    assert cfg.name == "HOUSEKEEPER_VIDEO"
    assert js.added and not js.updated


async def test_ensure_stream_updates_when_present() -> None:
    existing = _FakeStreamInfo(
        config=_FakeStreamConfig(name="HOUSEKEEPER_VIDEO"),
        state=_FakeState(),
    )
    js = _FakeJS(existing=existing)
    nc = _FakeNC(js=js)
    b = _make_bus(nc)
    async with b:
        created, cfg = await b.ensure_stream(
            NatsStreamConfig(name="HOUSEKEEPER_VIDEO", subjects=["video.events"])
        )
    assert created is False
    assert cfg.name == "HOUSEKEEPER_VIDEO"
    assert js.updated and not js.added


async def test_stream_info_returns_compact_dict() -> None:
    existing = _FakeStreamInfo(
        config=_FakeStreamConfig(name="HOUSEKEEPER_VIDEO", subjects=["video.events"]),
        state=_FakeState(messages=7, bytes=4096, first_seq=1, last_seq=7),
    )
    js = _FakeJS(existing=existing)
    nc = _FakeNC(js=js)
    b = _make_bus(nc)
    async with b:
        info = await b.stream_info()
    assert info["name"] == "HOUSEKEEPER_VIDEO"
    assert info["messages"] == 7
    assert info["last_seq"] == 7
    assert info["subjects"] == ["video.events"]


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------


async def _ok_tcp(*_args, **_kwargs):  # type: ignore[no-untyped-def]
    """Stub for asyncio.open_connection that pretends the TCP port is open."""

    class _DummyW:
        def close(self) -> None: ...

        async def wait_closed(self) -> None: ...

    return (None, _DummyW())


async def test_verify_reachable_returns_rtt(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bypass the real TCP probe; the bus is fake.
    monkeypatch.setattr("asyncio.open_connection", _ok_tcp)
    nc = _FakeNC(js=_FakeJS(), rtt_seconds=0.0025)
    b = _make_bus(nc)
    result = await b.verify()
    assert result.status is bus.BusStatus.REACHABLE
    assert "rtt=" in result.detail
    assert nc.drained


async def test_verify_unreachable_when_tcp_refused() -> None:
    # No server running on the configured port → real TCP probe should refuse.
    # Port 1 is reserved and reliably closed.
    cfg = NatsConfig(url="nats://127.0.0.1:1")

    async def _connector(_cfg):  # type: ignore[no-untyped-def]
        raise AssertionError("connector should not be invoked when TCP probe fails")

    b = bus.Bus(cfg, connector=_connector)
    result = await b.verify(timeout=1.0)
    assert result.status is bus.BusStatus.UNREACHABLE
    assert "refused" in result.detail.lower() or "errno" in result.detail.lower()
    assert not result.ok


async def test_verify_unreachable_when_connector_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TCP probe passes (we stub it) but the NATS handshake itself fails."""
    monkeypatch.setattr("asyncio.open_connection", _ok_tcp)
    b = _make_bus(raise_on_connect=OSError("handshake failed"))
    result = await b.verify()
    assert result.status is bus.BusStatus.UNREACHABLE
    assert "handshake failed" in result.detail


# ---------------------------------------------------------------------------
# Host/port parser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("nats://127.0.0.1:4222", ("127.0.0.1", 4222)),
        ("nats://example.com:4445", ("example.com", 4445)),
        ("nats://127.0.0.1", ("127.0.0.1", 4222)),
        ("nats://", ("127.0.0.1", 4222)),
    ],
)
def test_parse_nats_host_port(url: str, expected: tuple[str, int]) -> None:
    assert bus._parse_nats_host_port(url) == expected
