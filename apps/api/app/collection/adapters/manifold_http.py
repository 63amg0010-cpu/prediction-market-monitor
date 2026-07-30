"""Bounded public Manifold HTTP operations."""

from __future__ import annotations

import socket
from typing import Final

import httpx2

from .http_errors import (
    AdapterHttpError,
    HttpFailure,
    HttpFailureClassification,
    HttpFailureKind,
    TransportFailure,
    classify_http_failure,
)
from .manifold_contracts import (
    MAX_MANIFOLD_RESPONSE_BYTES,
    ManifoldCommentWire,
    ManifoldContractError,
    ManifoldMarket,
    parse_manifold_comments_json,
    parse_manifold_markets_json,
)
from .models import HttpMethod

MANIFOLD_API_ORIGIN: Final = "https://api.manifold.markets"
MANIFOLD_MARKETS_ROUTE: Final = "/v0/markets"
MANIFOLD_COMMENTS_ROUTE: Final = "/v0/comments"
MANIFOLD_TIMEOUT: Final = httpx2.Timeout(
    connect=5.0,
    read=30.0,
    write=10.0,
    pool=10.0,
)
MAX_MANIFOLD_PAGE_ITEMS: Final = 20
MARKETS_QUERY: Final = (
    ("limit", "20"),
    ("sort", "last-comment-time"),
    ("order", "desc"),
)


def create_manifold_http_client(
    base_url: str = MANIFOLD_API_ORIGIN,
) -> httpx2.AsyncClient:
    """Create the single-connection client for reviewed public API reads."""
    limits = httpx2.Limits(
        max_connections=1,
        max_keepalive_connections=1,
        keepalive_expiry=30.0,
    )
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=0,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=MANIFOLD_TIMEOUT,
        base_url=base_url,
        follow_redirects=False,
        trust_env=False,
    )


async def fetch_manifold_markets(
    client: httpx2.AsyncClient,
) -> tuple[ManifoldMarket, ...]:
    """Fetch at most twenty recent-comment markets through the reviewed route."""
    payload = await _fetch_json(
        client,
        MANIFOLD_MARKETS_ROUTE,
        MARKETS_QUERY,
    )
    try:
        markets = parse_manifold_markets_json(payload)
    except ManifoldContractError as error:
        raise _provider_response_error(
            error.code.value,
            MANIFOLD_MARKETS_ROUTE,
        ) from error
    if len(markets) > MAX_MANIFOLD_PAGE_ITEMS:
        error_code = "manifold_market_count_exceeded"
        raise _provider_response_error(
            error_code,
            MANIFOLD_MARKETS_ROUTE,
        )
    return markets


async def fetch_manifold_comments(
    client: httpx2.AsyncClient,
    market_id: str,
) -> tuple[ManifoldCommentWire, ...]:
    """Fetch at most twenty newest comments for one already parsed market ID."""
    payload = await _fetch_json(
        client,
        MANIFOLD_COMMENTS_ROUTE,
        (
            ("contractId", market_id),
            ("limit", "20"),
            ("page", "0"),
            ("order", "newest"),
        ),
    )
    try:
        comments = parse_manifold_comments_json(payload)
    except ManifoldContractError as error:
        raise _provider_response_error(
            error.code.value,
            MANIFOLD_COMMENTS_ROUTE,
        ) from error
    if len(comments) > MAX_MANIFOLD_PAGE_ITEMS:
        error_code = "manifold_comment_count_exceeded"
        raise _provider_response_error(
            error_code,
            MANIFOLD_COMMENTS_ROUTE,
        )
    return comments


async def _fetch_json(
    client: httpx2.AsyncClient,
    route: str,
    query: tuple[tuple[str, str], ...],
) -> bytes:
    request = client.build_request(
        HttpMethod.GET.value,
        route,
        params=query,
        headers={"Accept": "application/json"},
        timeout=MANIFOLD_TIMEOUT,
    )
    try:
        response = await client.send(
            request,
            stream=True,
            follow_redirects=False,
        )
    except httpx2.TimeoutException as error:
        raise _transport_error(TransportFailure.TIMEOUT, route) from error
    except httpx2.TransportError as error:
        raise _transport_error(TransportFailure.NETWORK, route) from error
    try:
        if not response.is_success:
            failure = HttpFailure(
                status_code=response.status_code,
                retry_after_header=_header(response.headers, "retry-after"),
            )
            raise AdapterHttpError(
                classification=classify_http_failure(failure),
                status_code=response.status_code,
                request_path=route,
            )
        content_type = (_header(response.headers, "content-type") or "").lower()
        if not content_type.startswith("application/json"):
            error_code = "manifold_unexpected_content_type"
            raise _provider_response_error(
                error_code,
                route,
            )
        content = bytearray()
        async for chunk in response.aiter_bytes():
            if len(content) + len(chunk) > MAX_MANIFOLD_RESPONSE_BYTES:
                error_code = "manifold_response_oversize"
                raise _provider_response_error(
                    error_code,
                    route,
                )
            content.extend(chunk)
        return bytes(content)
    finally:
        await response.aclose()


def _provider_response_error(code: str, route: str) -> AdapterHttpError:
    return AdapterHttpError(
        classification=HttpFailureClassification(
            kind=HttpFailureKind.TERMINAL,
            code=code,
            retry_after_seconds=None,
            backoff=None,
        ),
        status_code=200,
        request_path=route,
    )


def _transport_error(
    transport: TransportFailure,
    route: str,
) -> AdapterHttpError:
    return AdapterHttpError(
        classification=classify_http_failure(HttpFailure(transport=transport)),
        status_code=None,
        request_path=route,
    )


def _header(headers: httpx2.Headers, name: str) -> str | None:
    normalized_name = name.casefold()
    return next(
        (
            value
            for key, value in headers.multi_items()
            if key.casefold() == normalized_name
        ),
        None,
    )
