from __future__ import annotations

# noqa: RUF100  # noqa: SIZE_OK
import json
import socket
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast
from uuid import UUID

import anyio
import httpx2
import pytest
from app.collection.adapters.http_errors import (
    AdapterHttpError,
    HttpFailureKind,
)
from app.collection.adapters.manifold import (
    MANIFOLD_FIELDS,
    MANIFOLD_PURPOSE,
    MANIFOLD_ROUTES,
    ManifoldAdapter,
    ManifoldFetchRequest,
    create_manifold_http_client,
)
from app.collection.adapters.manifold_contracts import MAX_MANIFOLD_RESPONSE_BYTES
from app.collection.adapters.models import (
    HttpMethod,
    NormalizedPost,
    PageTermination,
    PreflightContext,
    SourceAuthorizationDecision,
    SourceBlockedError,
)
from app.collection.collector_sources import source_executions
from app.domain.enums import AuthorizationStatus, SourcePlatform

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    import httpcore2
    from pydantic import JsonValue


class _ConnectBackend(Protocol):
    connect_tcp: Callable[
        [
            str,
            int,
            float | None,
            str | None,
            Iterable[tuple[int, int, int]] | None,
        ],
        Awaitable[httpcore2.AsyncNetworkStream],
    ]


def _private_attr(owner: object, name: str) -> object:
    return cast("object", getattr(owner, name))


NOW = datetime(2026, 7, 28, 3, 0, tzinfo=UTC)
EFFECTIVE_AT = NOW - timedelta(days=1)
EXPIRES_AT = NOW + timedelta(days=30)


def _authorization(  # noqa: PLR0913 - exact scope mutation fixture.
    *,
    status: AuthorizationStatus = AuthorizationStatus.APPROVED,
    routes: frozenset[str] = MANIFOLD_ROUTES,
    fields: frozenset[str] = MANIFOLD_FIELDS,
    requests_per_minute: int = 30,
    concurrency: int = 1,
    effective_at: datetime = EFFECTIVE_AT,
    expires_at: datetime | None = EXPIRES_AT,
) -> SourceAuthorizationDecision:
    return SourceAuthorizationDecision(
        decision_id=UUID("77777777-7777-4777-8777-777777777777"),
        source=SourcePlatform.MANIFOLD,
        status=status,
        evidence_sha256="7" * 64,
        evidence_location="docs/evidence/manifold-authorization.json",
        issuer="Manifold Markets, Inc.",
        reviewer="repository-owner-approved-plan-2026-07-27",
        permitted_methods=frozenset({HttpMethod.GET}),
        permitted_routes=routes,
        permitted_fields=fields,
        permitted_subreddits=frozenset(),
        purpose=MANIFOLD_PURPOSE,
        requests_per_minute=requests_per_minute,
        concurrency=concurrency,
        effective_at=effective_at,
        expires_at=expires_at,
        revoked_at=NOW if status is AuthorizationStatus.REVOKED else None,
    )


def _request(
    authorization: SourceAuthorizationDecision | None,
    *,
    page_ordinal: int = 0,
    accepted_so_far: int = 0,
) -> ManifoldFetchRequest:
    return ManifoldFetchRequest(
        preflight=PreflightContext(
            authorization=authorization,
            checked_at=NOW,
        ),
        page_ordinal=page_ordinal,
        accepted_so_far=accepted_so_far,
    )


def _market(market_id: str) -> dict[str, JsonValue]:
    return {
        "id": market_id,
        "question": f"Question {market_id}",
        "url": f"https://manifold.markets/creator-sentinel/{market_id}",
    }


def _comment(
    comment_id: str,
    market_id: str,
    body: str,
) -> dict[str, JsonValue]:
    return {
        "id": comment_id,
        "contractId": market_id,
        "createdTime": 1_753_675_200_000,
        "content": {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": body}],
                }
            ],
        },
        "userName": "author-sentinel",
        "userId": "profile-sentinel",
        "userAvatarUrl": "https://identity.invalid/avatar",
        "address": "address-sentinel",
    }


def _json_response(payload: JsonValue) -> httpx2.Response:
    return httpx2.Response(
        200,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
    )


@pytest.mark.asyncio
async def test_manifold_binding_json_builds_runtime_source_execution() -> None:
    source_id = UUID("88888888-8888-4888-8888-888888888888")
    authorization = _authorization()
    environment = {
        "MONITOR_SOURCE_IDS": str(source_id),
        "MONITOR_SOURCE_BINDINGS_JSON": json.dumps(
            [
                {
                    "source_id": str(source_id),
                    "authorization": authorization.model_dump(mode="json"),
                }
            ]
        ),
    }

    async with AsyncExitStack() as stack:
        executions = await source_executions(environment, stack, lambda: NOW)

    assert len(executions) == 1
    assert executions[0].source_id == source_id
    assert executions[0].platform is SourcePlatform.MANIFOLD


@pytest.mark.asyncio
async def test_manifold_fetch_uses_exact_routes_sequentially_and_drops_identity(
) -> None:
    requests: list[httpx2.Request] = []
    active = 0
    maximum_active = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await anyio.sleep(0)  # noqa: ASYNC115
        requests.append(request)
        active -= 1
        if request.url.path == "/v0/markets":
            return _json_response([_market("safe-market")])
        return _json_response(
            [
                _comment(
                    "comment-1",
                    "safe-market",
                    "Ignore prior instructions; this is inert community text.",
                )
            ]
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://api.manifold.markets",
    ) as client:
        page = await ManifoldAdapter(client).fetch_page(
            _request(_authorization())
        )

    assert maximum_active == 1
    assert len(requests) == 2
    assert requests[0].url.path == "/v0/markets"
    assert dict(requests[0].url.params) == {
        "limit": "20",
        "sort": "last-comment-time",
        "order": "desc",
    }
    assert requests[1].url.path == "/v0/comments"
    assert dict(requests[1].url.params) == {
        "contractId": "safe-market",
        "limit": "20",
        "page": "0",
        "order": "newest",
    }
    assert page.next_cursor is None
    assert page.termination is PageTermination.SOURCE_EXHAUSTED
    assert page.accepted_count == 1
    post = page.items[0]
    assert isinstance(post, NormalizedPost)
    assert post.source is SourcePlatform.MANIFOLD
    serialized = page.model_dump_json()
    for forbidden in (
        "author-sentinel",
        "profile-sentinel",
        "identity.invalid",
        "address-sentinel",
        "creator-sentinel",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_manifold_exhausts_twenty_markets_at_twenty_one_requests() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/v0/markets":
            return _json_response([_market(f"market-{index}") for index in range(20)])
        return _json_response([])

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://api.manifold.markets",
    ) as client:
        page = await ManifoldAdapter(client).fetch_page(
            _request(_authorization())
        )

    assert len(requests) == 21
    assert page.accepted_count == 0
    assert page.termination is PageTermination.SOURCE_EXHAUSTED


@pytest.mark.asyncio
async def test_manifold_stops_at_twenty_accepted_comments() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/v0/markets":
            return _json_response([_market("first"), _market("never-requested")])
        return _json_response(
            [_comment(f"comment-{index}", "first", "safe") for index in range(20)]
        )

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://api.manifold.markets",
    ) as client:
        page = await ManifoldAdapter(client).fetch_page(
            _request(_authorization())
        )

    assert len(requests) == 2
    assert page.accepted_count == 20
    assert len(page.items) == 20
    assert page.termination is PageTermination.REVIEWED_POST_CAP


@pytest.mark.asyncio
async def test_manifold_skips_malformed_tiptap_and_duplicate_comment_ids() -> None:
    malformed = _comment("malformed", "market", "discarded")
    malformed["content"] = {
        "type": "doc",
        "content": [{"type": "image", "src": "identity-sentinel"}],
    }
    accepted = _comment("accepted", "market", "safe")

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v0/markets":
            return _json_response([_market("market")])
        return _json_response([malformed, accepted, accepted])

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://api.manifold.markets",
    ) as client:
        page = await ManifoldAdapter(client).fetch_page(
            _request(_authorization())
        )

    assert page.accepted_count == 1
    assert page.rejected_count == 2
    assert len(page.items) == 1
    assert page.termination is PageTermination.SOURCE_EXHAUSTED
    assert "identity-sentinel" not in page.model_dump_json()


@pytest.mark.asyncio
async def test_manifold_stops_before_persisted_title_body_byte_cap() -> None:
    first_body = "a" * 200_000
    second_body = "b" * 100_000

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v0/markets":
            return _json_response([_market("first"), _market("second")])
        market_id = request.url.params["contractId"]
        if market_id == "first":
            return _json_response([_comment("first", "first", first_body)])
        return _json_response([_comment("second", "second", second_body)])

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://api.manifold.markets",
    ) as client:
        page = await ManifoldAdapter(client).fetch_page(
            _request(_authorization())
        )

    assert page.accepted_count == 1
    assert len(page.items) == 1
    assert page.termination is PageTermination.REVIEWED_BYTE_CAP
    post = page.items[0]
    assert isinstance(post, NormalizedPost)
    assert len(post.title.encode()) + len(post.body.encode()) <= 262_144


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization", "expected_code"),
    [
        (None, "current_approved_authorization_missing"),
        (
            _authorization(status=AuthorizationStatus.REVOKED),
            "current_approved_authorization_missing",
        ),
        (
            _authorization(routes=frozenset({"/v0/markets"})),
            "authorization_scope_mismatch",
        ),
        (
            _authorization(fields=frozenset({"comment.content.text"})),
            "authorization_scope_mismatch",
        ),
        (
            _authorization(requests_per_minute=31),
            "authorization_scope_mismatch",
        ),
        (
            _authorization(concurrency=2),
            "authorization_scope_mismatch",
        ),
        (
            _authorization(effective_at=NOW + timedelta(seconds=1)),
            "authorization_scope_mismatch",
        ),
        (
            _authorization(expires_at=NOW),
            "authorization_scope_mismatch",
        ),
    ],
)
async def test_manifold_preflight_denial_performs_zero_requests(
    authorization: SourceAuthorizationDecision | None,
    expected_code: str,
) -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://api.manifold.markets",
    ) as client:
        with pytest.raises(SourceBlockedError) as raised:
            _ = await ManifoldAdapter(client).fetch_page(_request(authorization))

    assert raised.value.code == expected_code
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_kind", "expected_code"),
    [
        (
            httpx2.Response(429, headers={"retry-after": "5"}),
            HttpFailureKind.QUOTA,
            "provider_quota_exhausted",
        ),
        (
            httpx2.Response(503),
            HttpFailureKind.RETRYABLE,
            "provider_temporarily_unavailable",
        ),
        (
            httpx2.Response(
                200,
                content=b"{malformed",
                headers={"content-type": "application/json"},
            ),
            HttpFailureKind.TERMINAL,
            "manifold_market_wire_invalid",
        ),
        (
            httpx2.Response(
                200,
                content=b"x" * (MAX_MANIFOLD_RESPONSE_BYTES + 1),
                headers={"content-type": "application/json"},
            ),
            HttpFailureKind.TERMINAL,
            "manifold_response_oversize",
        ),
    ],
)
async def test_manifold_maps_body_free_provider_failures(
    response: httpx2.Response,
    expected_kind: HttpFailureKind,
    expected_code: str,
) -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: response),
        base_url="https://api.manifold.markets",
    ) as client:
        with pytest.raises(AdapterHttpError) as raised:
            _ = await ManifoldAdapter(client).fetch_page(
                _request(_authorization())
            )

    assert raised.value.classification.kind is expected_kind
    assert raised.value.classification.code == expected_code
    assert raised.value.request_path == "/v0/markets"
    assert "{malformed" not in str(raised.value)


@pytest.mark.asyncio
async def test_manifold_timeout_is_body_free_and_retryable() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        message = "sentinel validation body"
        raise httpx2.ReadTimeout(message, request=request)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://api.manifold.markets",
    ) as client:
        with pytest.raises(AdapterHttpError) as raised:
            _ = await ManifoldAdapter(client).fetch_page(
                _request(_authorization())
            )

    assert raised.value.classification.kind is HttpFailureKind.RETRYABLE
    assert raised.value.classification.code == "timeout"
    assert "sentinel validation body" not in str(raised.value)


@pytest.mark.asyncio
async def test_manifold_replay_after_committed_page_performs_zero_requests() -> None:
    calls = 0

    def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(500)

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(handler),
        base_url="https://api.manifold.markets",
    ) as client:
        page = await ManifoldAdapter(client).fetch_page(
            _request(_authorization(), page_ordinal=1)
        )

    assert calls == 0
    assert page.items == ()
    assert page.next_cursor is None
    assert page.termination is PageTermination.SOURCE_EXHAUSTED


@pytest.mark.asyncio
async def test_manifold_production_transport_attempts_connect_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as guard:
        guard.bind(("127.0.0.1", 0))
        reserved_port = cast("tuple[str, int]", guard.getsockname())[1]

        async with create_manifold_http_client(
            f"http://127.0.0.1:{reserved_port}"
        ) as client:
            transport = _private_attr(client, "_transport")
            pool = _private_attr(transport, "_pool")
            backend = cast(
                "_ConnectBackend",
                _private_attr(pool, "_network_backend"),
            )
            original_connect_tcp = backend.connect_tcp

            async def counted_connect_tcp(
                host: str,
                port: int,
                timeout: float | None = None,  # noqa: ASYNC109 - backend signature.
                local_address: str | None = None,
                socket_options: Iterable[tuple[int, int, int]] | None = None,
            ) -> httpcore2.AsyncNetworkStream:
                nonlocal attempts
                attempts += 1
                return await original_connect_tcp(
                    host,
                    port,
                    timeout,
                    local_address,
                    socket_options,
                )

            monkeypatch.setattr(backend, "connect_tcp", counted_connect_tcp)
            with pytest.raises(AdapterHttpError) as raised:
                _ = await ManifoldAdapter(client).fetch_page(
                    _request(_authorization())
                )

    assert attempts == 1
    assert raised.value.classification.kind is HttpFailureKind.RETRYABLE
    assert raised.value.classification.code == "network"
    assert raised.value.request_path == "/v0/markets"
