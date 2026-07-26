"""Bounded HTTP transport shared by collector and verifier workflows."""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING, Self

import anyio
import httpx2
from pydantic import ValidationError

from app.core.errors import ErrorEnvelope

from .cli_config import CliError, json_bytes

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from app.domain.types import JsonValue

CONTROL_PLANE_UNAVAILABLE = "control_plane_unavailable"
CONTROL_PLANE_READ_TIMEOUT_SECONDS = 60.0
HTTP_SERVER_ERROR = 500


class ControlPlaneTransport:
    """Own the bounded HTTP client and in-memory bearer token."""

    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        """Bind a fail-closed HTTP client to one API base URL."""
        self._token: str | None = None
        client_transport = transport or httpx2.AsyncHTTPTransport(
            http2=True,
            retries=3,
            limits=httpx2.Limits(
                max_connections=200,
                max_keepalive_connections=40,
                keepalive_expiry=30.0,
            ),
            socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
        )
        self._client: httpx2.AsyncClient = httpx2.AsyncClient(
            base_url=base_url,
            timeout=httpx2.Timeout(
                connect=5.0,
                read=CONTROL_PLANE_READ_TIMEOUT_SECONDS,
                write=10.0,
                pool=10.0,
            ),
            follow_redirects=False,
            trust_env=False,
            transport=client_transport,
        )

    async def __aenter__(self) -> Self:
        """Enter the managed HTTP client scope."""
        return self

    async def __aexit__(
        self,
        _t: type[BaseException] | None,
        _e: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        """Close pooled sockets on every outcome."""
        await self._client.aclose()

    def set_token(self, token: str) -> None:
        """Keep one short-lived bearer token in memory for this run."""
        self._token = token

    async def authorized_request(
        self, method: str, path: str, token: str, content: bytes | None = None
    ) -> httpx2.Response:
        """Call one scoped API endpoint with an in-memory bearer token."""
        return await self._request(
            method,
            path,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            content=content,
        )

    async def collector_request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, JsonValue] | None = None,
        *,
        allow_conflict: bool = False,
    ) -> httpx2.Response:
        """Call one collector endpoint using the run's in-memory token."""
        if self._token is None:
            error_code = "collector_not_authenticated"
            raise CliError(error_code)
        return await self._request(
            method,
            path,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
            content=None if payload is None else json_bytes(dict(payload)),
            allowed_status=409 if allow_conflict else None,
        )

    async def _request(  # noqa: PLR0913 - bounded HTTP request parameters.
        self,
        method: str,
        path: str,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        content: bytes | None = None,
        allowed_status: int | None = None,
    ) -> httpx2.Response:
        for attempt in range(2):
            try:
                response = await self._client.request(
                    method, path, headers=headers, params=params, content=content
                )
            except httpx2.TransportError as error:
                if attempt:
                    raise CliError(CONTROL_PLANE_UNAVAILABLE) from error
                await anyio.sleep(1)
                continue
            if response.status_code == allowed_status:
                return response
            if response.status_code < HTTP_SERVER_ERROR:
                _ = response.raise_for_status()
                return response
            if attempt:
                raise CliError(_redacted_unavailable_code(response))
            await anyio.sleep(1)
        raise CliError(CONTROL_PLANE_UNAVAILABLE)


def _redacted_unavailable_code(response: httpx2.Response) -> str:
    status = f"http_{response.status_code}"
    try:
        code = ErrorEnvelope.model_validate_json(response.content).error.code.value
    except ValidationError:
        return f"{CONTROL_PLANE_UNAVAILABLE}:{status}"
    return f"{CONTROL_PLANE_UNAVAILABLE}:{status}:{code}"


__all__ = ("CONTROL_PLANE_READ_TIMEOUT_SECONDS", "ControlPlaneTransport")
