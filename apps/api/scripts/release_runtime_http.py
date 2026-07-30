"""Read-only HTTP and acceptance-capture release adapters."""

# ruff: noqa: D102, D107, PLR2004
# pyright: reportUnannotatedClassAttribute=false

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Final, Protocol
from urllib.parse import urlsplit

import httpx2

from scripts.release_chain_capture import CaptureObservation

MAX_HTTP_BYTES: Final = 1_048_576


class HttpRuntimeError(RuntimeError):
    """Stable HTTP boundary error."""


class Response(Protocol):
    status_code: int
    content: bytes
    headers: Mapping[str, str]


Get = Callable[[str], Response]
Clock = Callable[[], datetime]


def _https_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        msg = "http_url_invalid"
        raise HttpRuntimeError(msg)


class ReadOnlyHttpProbe:
    """Bounded HTTPS GET adapter with redirects disabled."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_bytes: int = MAX_HTTP_BYTES,
        get: Get | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_bytes <= 0:
            msg = "http_policy_invalid"
            raise HttpRuntimeError(msg)
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._get = get

    def fetch(self, url: str) -> bytes:
        _https_url(url)
        try:
            if self._get is not None:
                response = self._get(url)
            else:
                with httpx2.Client(
                    follow_redirects=False,
                    timeout=self._timeout,
                ) as client:
                    response = client.get(
                        url,
                        headers={"accept": "application/json"},
                    )
        except (httpx2.HTTPError, OSError) as error:
            msg = "http_probe_failed"
            raise HttpRuntimeError(msg) from error
        if 300 <= response.status_code < 400:
            msg = "http_redirect_forbidden"
            raise HttpRuntimeError(msg)
        if response.status_code != 200:
            msg = "http_status_rejected"
            raise HttpRuntimeError(msg)
        length = response.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > self._max_bytes:
                    msg = "http_body_too_large"
                    raise HttpRuntimeError(msg)
            except ValueError as error:
                msg = "http_content_length_invalid"
                raise HttpRuntimeError(msg) from error
        if not response.content or len(response.content) > self._max_bytes:
            msg = "http_body_size_invalid"
            raise HttpRuntimeError(msg)
        return response.content


@dataclass(frozen=True, slots=True)
class HttpCaptureSpec:
    """One public-safe current-state capture endpoint."""

    url: str
    tool_version: str


class AcceptanceHttpCaptureProvider:
    """Hash current HTTP observations without exposing response content."""

    def __init__(
        self,
        specs: Mapping[str, HttpCaptureSpec],
        *,
        probe: ReadOnlyHttpProbe,
        clock: Clock,
    ) -> None:
        if not specs:
            msg = "capture_specs_empty"
            raise HttpRuntimeError(msg)
        for spec in specs.values():
            _https_url(spec.url)
            if not spec.tool_version:
                msg = "capture_tool_version_empty"
                raise HttpRuntimeError(msg)
        self._specs = dict(specs)
        self._probe = probe
        self._clock = clock

    def capture(self, member_name: str) -> CaptureObservation:
        spec = self._specs.get(member_name)
        if spec is None:
            msg = "capture_member_unknown"
            raise HttpRuntimeError(msg)
        raw = self._probe.fetch(spec.url)
        return CaptureObservation(
            evidence_sha256=sha256(raw).hexdigest(),
            captured_at=self._clock(),
            tool_version=spec.tool_version,
            accepted=True,
        )


__all__ = (
    "AcceptanceHttpCaptureProvider",
    "HttpCaptureSpec",
    "HttpRuntimeError",
    "ReadOnlyHttpProbe",
)
