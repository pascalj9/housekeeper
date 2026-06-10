"""ntfy notifier.

Thin wrapper around the ntfy HTTP API plus a ``verify`` probe that the future
``housekeeper doctor`` calls. The notifier never spawns or supervises ntfy —
that's the operator's job (launchd on macOS, systemd --user on Linux/WSL).

Reference: https://docs.ntfy.sh/publish/
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import httpx

from housekeeper.services import NtfyConfig, load_config

# ---------------------------------------------------------------------------
# Verification result (mirrors housekeeper.models.Status for consistency)
# ---------------------------------------------------------------------------


class NtfyStatus(StrEnum):
    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    ERROR = "error"


@dataclass(frozen=True)
class NtfyVerifyResult:
    status: NtfyStatus
    endpoint: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status is NtfyStatus.REACHABLE


# ---------------------------------------------------------------------------
# Priority levels (mirror ntfy's 1..5 scale, named for readability)
# ---------------------------------------------------------------------------


class Priority(StrEnum):
    """ntfy priority levels — string values map onto the ntfy header value."""

    MIN = "1"
    LOW = "2"
    DEFAULT = "3"
    HIGH = "4"
    URGENT = "5"


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------


class NtfyNotifier:
    """Publish messages to an ntfy topic over HTTP.

    The notifier is stateless apart from the underlying ``httpx.Client``; it's
    safe to keep one instance for the lifetime of the process.
    """

    def __init__(
        self,
        config: NtfyConfig | None = None,
        *,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.config = config or load_config().ntfy
        self._client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    # -------- public API -------------------------------------------------

    def send(
        self,
        *,
        title: str,
        body: str,
        priority: Priority | str = Priority.DEFAULT,
        tags: Sequence[str] = (),
        click_url: str | None = None,
    ) -> httpx.Response:
        """Publish one notification. Returns the raw ntfy response."""
        headers: dict[str, str] = {
            "Title": title,
            "Priority": str(priority),
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        if click_url:
            headers["Click"] = click_url
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"

        response = self._client.post(
            self.config.topic_url,
            content=body.encode("utf-8"),
            headers=headers,
        )
        response.raise_for_status()
        return response

    def verify(self) -> NtfyVerifyResult:
        """Probe the ntfy server's base URL.

        We only check that the server speaks HTTP and returns a 2xx/3xx — we
        don't try to publish, so this is safe to run from ``doctor`` without
        spamming the phone.
        """
        try:
            resp = self._client.get(self.config.endpoint, timeout=2.0)
        except httpx.HTTPError as exc:
            return NtfyVerifyResult(NtfyStatus.UNREACHABLE, self.config.endpoint, str(exc))
        if 200 <= resp.status_code < 400:
            return NtfyVerifyResult(
                NtfyStatus.REACHABLE,
                self.config.endpoint,
                f"HTTP {resp.status_code}",
            )
        return NtfyVerifyResult(NtfyStatus.ERROR, self.config.endpoint, f"HTTP {resp.status_code}")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # -------- context manager --------------------------------------------

    def __enter__(self) -> NtfyNotifier:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
