from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2
import pytest
from app.collection.adapters.models import (
    BlockedKind,
    PreflightBlocked,
    SourceBlockedError,
)
from app.collection.adapters.reddit import RedditAdapter, RedditFetchRequest
from app.domain.enums import AuthorizationStatus

from .factories import (
    reddit_authorization,
    reddit_context,
    reddit_credentials,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.asyncio
async def test_network_is_forbidden_without_current_approval() -> None:
    # Given
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(500)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        adapter = RedditAdapter(client)
        request = RedditFetchRequest(
            preflight=reddit_context(None),
            credentials=reddit_credentials(),
            cursor=None,
            accepted_so_far=0,
            page_size=20,
        )

        # When / Then
        with pytest.raises(SourceBlockedError) as raised:
            _ = await adapter.fetch_page(request)

    assert raised.value.kind is BlockedKind.BLOCKED_AUTHORIZATION
    assert calls == []


@pytest.mark.asyncio
async def test_revocation_between_pages_stops_before_second_request(
    reddit_page_response: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    # Given
    calls: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return reddit_page_response(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        adapter = RedditAdapter(client)
        first = await adapter.fetch_page(
            RedditFetchRequest(
                preflight=reddit_context(reddit_authorization()),
                credentials=reddit_credentials(),
                cursor=None,
                accepted_so_far=0,
                page_size=2,
            )
        )
        revoked = reddit_authorization(AuthorizationStatus.REVOKED)

        # When / Then
        with pytest.raises(SourceBlockedError):
            _ = await adapter.fetch_page(
                RedditFetchRequest(
                    preflight=reddit_context(revoked),
                    credentials=reddit_credentials(),
                    cursor=first.next_cursor,
                    accepted_so_far=first.accepted_count,
                    page_size=2,
                )
            )

    assert len(calls) == 1


def test_preflight_rejects_scope_broader_than_reviewed_subreddits() -> None:
    # Given
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(500))
    )
    adapter = RedditAdapter(client)
    decision = reddit_authorization(
        subreddits=frozenset({"Polymarket", "Kalshi", "PredictionMarkets", "stocks"})
    )

    # When
    result = adapter.preflight(reddit_context(decision))

    # Then
    assert isinstance(result, PreflightBlocked)
    assert result.kind is BlockedKind.BLOCKED_AUTHORIZATION
    assert result.code == "authorization_scope_mismatch"
