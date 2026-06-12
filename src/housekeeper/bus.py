"""NATS event-bus wrapper.

Thin async layer over ``nats-py`` so the rest of the codebase doesn't import
``nats`` directly. Centralises:

* connection lifecycle + reconnect policy (read from services config),
* JetStream stream provisioning (`ensure_stream`),
* a single ``verify()`` probe used by Phase 0.6 ``housekeeper doctor``,
* a tiny set of convenience helpers (``publish``, ``subscribe_iter``).

Design notes
------------
* The wrapper is async. The CLI commands wrap it in ``asyncio.run``; production
  services (perception, agent, chat) will run in their own event loop and
  inject an existing connection.
* No global state. Tests build a ``Bus`` with an injected ``ConnectorFn`` so
  they can stub the NATS client.
* JetStream usage is opt-in per call — most subjects are plain pub/sub. Only
  ``video.events`` is durable in v1.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import nats
from nats.aio.client import Client as NATSClient
from nats.aio.msg import Msg
from nats.js.api import RetentionPolicy, StorageType, StreamConfig
from nats.js.errors import NotFoundError

from housekeeper.services import NatsConfig, NatsStreamConfig, load_config

# A connector returns a connected NATSClient. Pluggable for tests.
ConnectorFn = Callable[[NatsConfig], Awaitable[NATSClient]]


# ---------------------------------------------------------------------------
# Verification result (mirrors models.Status / NtfyStatus shape)
# ---------------------------------------------------------------------------


class BusStatus(StrEnum):
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    ERROR = "error"


@dataclass(frozen=True)
class BusVerifyResult:
    status: BusStatus
    url: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is BusStatus.REACHABLE


# ---------------------------------------------------------------------------
# Default connector
# ---------------------------------------------------------------------------


async def _default_connector(cfg: NatsConfig) -> NATSClient:
    """Open a NATS connection using the project's reconnect policy.

    We pass ``allow_reconnect=False`` here so a one-shot ``verify()`` doesn't
    spin in the background trying to reconnect after we've already given up.
    Long-running services (perception, agent) build their own ``Bus`` and
    can re-enable reconnection.
    """
    return await nats.connect(
        servers=[cfg.url],
        allow_reconnect=False,
        connect_timeout=2,
        max_reconnect_attempts=cfg.max_reconnect_attempts,
        reconnect_time_wait=cfg.reconnect_time_wait_seconds,
        name="housekeeper",
    )


# ---------------------------------------------------------------------------
# Bus
# ---------------------------------------------------------------------------


class Bus:
    """High-level wrapper over a NATS client.

    Use as an async context manager::

        async with Bus() as bus:
            await bus.publish("video.events", b"{...}")
    """

    def __init__(
        self,
        config: NatsConfig | None = None,
        *,
        connector: ConnectorFn | None = None,
    ) -> None:
        self.config = config or load_config().nats
        self._connector = connector or _default_connector
        self._nc: NATSClient | None = None

    # -------- lifecycle --------------------------------------------------

    async def connect(self) -> None:
        if self._nc is None or not self._nc.is_connected:
            self._nc = await self._connector(self.config)

    async def close(self) -> None:
        if self._nc is not None and self._nc.is_connected:
            await self._nc.drain()
        self._nc = None

    async def __aenter__(self) -> Bus:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    @property
    def client(self) -> NATSClient:
        if self._nc is None:
            raise RuntimeError("Bus.connect() must be called before use")
        return self._nc

    # -------- pub/sub ----------------------------------------------------

    async def publish(
        self,
        subject: str,
        payload: bytes,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        await self.client.publish(subject, payload, headers=headers)

    async def subscribe_iter(self, subject: str, *, queue: str | None = None) -> AsyncIterator[Msg]:
        """Yield messages on ``subject`` until cancelled."""
        sub = await self.client.subscribe(subject, queue=queue or "")
        try:
            async for msg in sub.messages:
                yield msg
        finally:
            with contextlib.suppress(Exception):
                await sub.unsubscribe()

    # -------- JetStream --------------------------------------------------

    async def ensure_stream(
        self, stream: NatsStreamConfig | None = None
    ) -> tuple[bool, StreamConfig]:
        """Create or update the JetStream stream for video.events.

        Returns ``(created, config)`` — ``created`` is True if the stream
        didn't exist beforehand.
        """
        spec = stream or self.config.stream
        js = self.client.jetstream()
        wanted = StreamConfig(
            name=spec.name,
            subjects=spec.subjects,
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            # nats-py expects max_age in seconds and converts to ns itself.
            max_age=spec.max_age_seconds,
            max_bytes=spec.max_bytes,
        )
        try:
            await js.stream_info(spec.name)
            await js.update_stream(config=wanted)
            return (False, wanted)
        except NotFoundError:
            await js.add_stream(config=wanted)
            return (True, wanted)

    async def stream_info(self) -> dict[str, Any]:
        """Return a compact dict describing the configured JetStream stream."""
        js = self.client.jetstream()
        info = await js.stream_info(self.config.stream.name)
        return {
            "name": info.config.name,
            "subjects": list(info.config.subjects or []),
            "messages": info.state.messages,
            "bytes": info.state.bytes,
            "first_seq": info.state.first_seq,
            "last_seq": info.state.last_seq,
        }

    # -------- probes -----------------------------------------------------

    async def verify(self, *, timeout: float = 3.0) -> BusVerifyResult:
        """Connect → ping → disconnect. Used by ``housekeeper doctor``.

        We probe the TCP port directly before invoking nats-py, because
        nats-py's connect path retries internally and swallows the root
        ``ConnectionRefused`` into a hard-to-parse ``CancelledError``. A
        targeted TCP probe gives the user an actionable message
        (``connection refused``, ``host unreachable``, …).
        """
        nats_logger = logging.getLogger("nats")
        prior_level = nats_logger.level
        nats_logger.setLevel(logging.CRITICAL)
        try:
            host, port = _parse_nats_host_port(self.config.url)
            try:
                await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
            except (TimeoutError, OSError) as exc:
                return BusVerifyResult(BusStatus.UNREACHABLE, self.config.url, _describe_exc(exc))

            try:
                await asyncio.wait_for(self.connect(), timeout=timeout)
            except Exception as exc:
                return BusVerifyResult(BusStatus.UNREACHABLE, self.config.url, _describe_exc(exc))

            try:
                rtt = await asyncio.wait_for(self.client.rtt(), timeout=timeout)
            except Exception as exc:
                await self.close()
                return BusVerifyResult(
                    BusStatus.ERROR, self.config.url, f"rtt: {_describe_exc(exc)}"
                )

            await self.close()
            return BusVerifyResult(
                BusStatus.REACHABLE,
                self.config.url,
                f"rtt={rtt * 1000:.1f}ms",
            )
        finally:
            nats_logger.setLevel(prior_level)


def _parse_nats_host_port(url: str) -> tuple[str, int]:
    """Pull host/port out of a ``nats://host:port`` URL with sane defaults."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    return (parsed.hostname or "127.0.0.1", parsed.port or 4222)


def _describe_exc(exc: BaseException) -> str:
    """nats-py sometimes raises exceptions with empty messages; fall back to
    the exception class name + any chained cause."""
    msg = str(exc).strip()
    if not msg:
        cause = exc.__cause__ or exc.__context__
        msg = f"{type(cause).__name__}: {cause}" if cause is not None else type(exc).__name__
    return msg
