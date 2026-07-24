"""Reddit OAuth wire operations and redacted provider failures."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import httpx2
from pydantic import ValidationError

from .http_errors import (
    AdapterHttpError,
    HttpFailure,
    HttpFailureClassification,
    HttpFailureKind,
    TransportFailure,
    classify_http_failure,
)
from .models import HttpMethod, RateLimitSnapshot
from .reddit_contracts import (
    REDDIT_API_URL,
    REDDIT_ROUTE,
    REDDIT_TIMEOUT,
    RedditFetchRequest,
)
from .reddit_models import RedditListing


@dataclass(frozen=True, slots=True)
class _RedditWirePage:
    listing: RedditListing
    rate_limit: RateLimitSnapshot


async def fetch_reddit_listing(
    client: httpx2.AsyncClient,
    request: RedditFetchRequest,
    limit: int,
) -> _RedditWirePage:
    """Fetch and parse one bounded Reddit OAuth listing response."""
    params = {"limit": str(limit), "raw_json": "1"}
    if request.cursor is not None:
        params["after"] = request.cursor
    wire_request = client.build_request(
        HttpMethod.GET.value,
        REDDIT_API_URL,
        params=params,
        headers={
            "Authorization": (
                f"Bearer {request.credentials.access_token.get_secret_value()}"
            ),
            "User-Agent": request.credentials.user_agent,
            "Accept": "application/json",
        },
        timeout=REDDIT_TIMEOUT,
    )
    try:
        response = await client.send(wire_request, follow_redirects=False)
    except httpx2.TimeoutException as error:
        raise _transport_error(TransportFailure.TIMEOUT) from error
    except httpx2.TransportError as error:
        raise _transport_error(TransportFailure.NETWORK) from error
    if not response.is_success:
        failure = HttpFailure(
            status_code=response.status_code,
            retry_after_header=_header(response.headers, "retry-after"),
            rate_reset_header=_header(response.headers, "x-ratelimit-reset"),
        )
        raise AdapterHttpError(
            classification=classify_http_failure(failure),
            status_code=response.status_code,
            request_path=REDDIT_ROUTE,
        )
    if (
        not (_header(response.headers, "content-type") or "")
        .lower()
        .startswith("application/json")
    ):
        raise provider_response_error(
            HttpFailureKind.POLICY,
            "unexpected_content_type",
        )
    try:
        listing = RedditListing.model_validate_json(response.content)
    except ValidationError as error:
        raise provider_response_error(
            HttpFailureKind.TERMINAL,
            "provider_contract_invalid",
        ) from error
    if len(listing.data.children) > limit:
        raise provider_response_error(
            HttpFailureKind.TERMINAL,
            "provider_page_limit_exceeded",
        )
    return _RedditWirePage(listing=listing, rate_limit=_rate_limit(response.headers))


def create_reddit_http_client() -> httpx2.AsyncClient:
    """Create the bounded HTTPS client used only for Reddit OAuth requests."""
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
        timeout=REDDIT_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
    )


def provider_response_error(kind: HttpFailureKind, code: str) -> AdapterHttpError:
    """Create a body-free error for an invalid successful provider response."""
    return AdapterHttpError(
        classification=HttpFailureClassification(
            kind=kind,
            code=code,
            retry_after_seconds=None,
            backoff=None,
        ),
        status_code=200,
        request_path=REDDIT_ROUTE,
    )


def _transport_error(transport: TransportFailure) -> AdapterHttpError:
    failure = HttpFailure(transport=transport)
    return AdapterHttpError(
        classification=classify_http_failure(failure),
        status_code=None,
        request_path=REDDIT_ROUTE,
    )


def _rate_limit(headers: httpx2.Headers) -> RateLimitSnapshot:
    used = _decimal_header(_header(headers, "x-ratelimit-used"))
    remaining = _decimal_header(_header(headers, "x-ratelimit-remaining"))
    reset = _decimal_header(_header(headers, "x-ratelimit-reset"))
    retry_after = reset if remaining is not None and remaining <= 0 else None
    return RateLimitSnapshot(
        used=used,
        remaining=remaining,
        reset_after_seconds=reset,
        retry_after_seconds=retry_after,
    )


def _decimal_header(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None


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
