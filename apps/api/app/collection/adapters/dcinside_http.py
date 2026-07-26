"""Bounded DCInside gallery HTTP operations."""

from __future__ import annotations

import socket

import httpx2

from .dcinside_contracts import (
    DCINSIDE_LIST_ROUTE,
    DCINSIDE_LIST_URL,
    DCINSIDE_ORIGIN,
    DCINSIDE_TIMEOUT,
    DCINSIDE_VIEW_ROUTE,
    DCInsideFetchRequest,
)
from .dcinside_html import (
    DCInsideHtmlContractError,
    DCInsidePostDocument,
    parse_post_document,
    parse_post_ids,
)
from .http_errors import (
    AdapterHttpError,
    HttpFailure,
    HttpFailureClassification,
    HttpFailureKind,
    TransportFailure,
    classify_http_failure,
)
from .models import HttpMethod


async def fetch_dcinside_documents(
    client: httpx2.AsyncClient,
    request: DCInsideFetchRequest,
    limit: int,
) -> tuple[DCInsidePostDocument, ...]:
    """Fetch one list page and its bounded set of author-free post views."""
    list_source = await _fetch_html(
        client,
        DCINSIDE_LIST_URL,
        request.user_agent,
        DCINSIDE_LIST_ROUTE,
    )
    try:
        post_ids = parse_post_ids(list_source, limit)
    except DCInsideHtmlContractError as error:
        raise _provider_response_error(error.code, DCINSIDE_LIST_ROUTE) from error
    documents: list[DCInsidePostDocument] = []
    for post_id in post_ids:
        route = DCINSIDE_VIEW_ROUTE.format(post_id=post_id)
        view_source = await _fetch_html(
            client,
            f"{DCINSIDE_ORIGIN}{route}",
            request.user_agent,
            route,
        )
        try:
            documents.append(parse_post_document(view_source, post_id))
        except DCInsideHtmlContractError as error:
            raise _provider_response_error(error.code, route) from error
    return tuple(documents)


def create_dcinside_http_client() -> httpx2.AsyncClient:
    """Create the single-connection HTTPS client for the reviewed gallery."""
    limits = httpx2.Limits(
        max_connections=1,
        max_keepalive_connections=1,
        keepalive_expiry=30.0,
    )
    transport = httpx2.AsyncHTTPTransport(
        http2=True,
        retries=3,
        limits=limits,
        socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
    )
    return httpx2.AsyncClient(
        transport=transport,
        timeout=DCINSIDE_TIMEOUT,
        follow_redirects=False,
        trust_env=False,
    )


async def _fetch_html(
    client: httpx2.AsyncClient,
    url: str,
    user_agent: str,
    request_path: str,
) -> str:
    wire_request = client.build_request(
        HttpMethod.GET.value,
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html",
        },
        timeout=DCINSIDE_TIMEOUT,
    )
    try:
        response = await client.send(wire_request, follow_redirects=False)
    except httpx2.TimeoutException as error:
        raise _transport_error(TransportFailure.TIMEOUT, request_path) from error
    except httpx2.TransportError as error:
        raise _transport_error(TransportFailure.NETWORK, request_path) from error
    if not response.is_success:
        failure = HttpFailure(
            status_code=response.status_code,
            retry_after_header=_header(response.headers, "retry-after"),
        )
        raise AdapterHttpError(
            classification=classify_http_failure(failure),
            status_code=response.status_code,
            request_path=request_path,
        )
    content_type = (_header(response.headers, "content-type") or "").lower()
    if not content_type.startswith("text/html"):
        error_code = "unexpected_content_type"
        raise _provider_response_error(error_code, request_path)
    return response.text


def _provider_response_error(code: str, request_path: str) -> AdapterHttpError:
    return AdapterHttpError(
        classification=HttpFailureClassification(
            kind=HttpFailureKind.TERMINAL,
            code=code,
            retry_after_seconds=None,
            backoff=None,
        ),
        status_code=200,
        request_path=request_path,
    )


def _transport_error(
    transport: TransportFailure,
    request_path: str,
) -> AdapterHttpError:
    return AdapterHttpError(
        classification=classify_http_failure(HttpFailure(transport=transport)),
        status_code=None,
        request_path=request_path,
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
