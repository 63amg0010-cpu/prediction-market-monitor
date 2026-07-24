from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import httpx2
import pytest
from app.collection.adapters.http_errors import (
    AdapterHttpError,
    HttpFailure,
    HttpFailureKind,
    classify_http_failure,
)
from app.collection.adapters.models import PageTermination
from app.collection.adapters.reddit import RedditAdapter, RedditFetchRequest
from pydantic import TypeAdapter

from .factories import (
    TEST_ACCESS_TOKEN,
    reddit_authorization,
    reddit_context,
    reddit_credentials,
)

if TYPE_CHECKING:
    from collections.abc import Callable

TIMEOUT_ADAPTER: TypeAdapter[dict[str, float]] = TypeAdapter(dict[str, float])


@pytest.mark.asyncio
async def test_oauth_request_uses_https_headers_timeout_and_pagination(
    reddit_page_response: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    # Given
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
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

        # When
        second = await adapter.fetch_page(
            RedditFetchRequest(
                preflight=reddit_context(reddit_authorization()),
                credentials=reddit_credentials(),
                cursor=first.next_cursor,
                accepted_so_far=first.accepted_count,
                page_size=2,
            )
        )

    # Then
    assert requests[0].url.scheme == "https"
    assert requests[0].url.host == "oauth.reddit.com"
    assert requests[0].url.path == "/r/Polymarket+Kalshi+PredictionMarkets/new"
    assert requests[0].url.params["limit"] == "2"
    assert requests[0].url.params["raw_json"] == "1"
    assert requests[1].url.params["after"] == "t3_after_page_1"
    assert requests[0].headers["authorization"] == f"Bearer {TEST_ACCESS_TOKEN}"
    assert requests[0].headers["user-agent"] == "prediction-market-monitor/tests-only"
    timeout = TIMEOUT_ADAPTER.validate_python(requests[0].extensions["timeout"])
    assert timeout == {"connect": 5.0, "read": 30.0, "write": 10.0, "pool": 10.0}
    assert second.next_cursor is None
    assert second.termination is PageTermination.SOURCE_EXHAUSTED


@pytest.mark.asyncio
async def test_source_cap_reduces_provider_limit_and_terminates() -> None:
    # Given
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "kind": "Listing",
                "data": {
                    "after": "t3_cap_cursor",
                    "children": [
                        {
                            "kind": "t3",
                            "data": {
                                "id": "cap1",
                                "subreddit": "Polymarket",
                                "title": "Cap item",
                                "selftext": "Body",
                                "permalink": "/r/Polymarket/comments/cap1/cap_item/",
                                "created_utc": 1784570400.0,
                                "num_comments": 0,
                                "score": 0,
                                "author": "tests-only-cap-author",
                            },
                        }
                    ],
                },
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        adapter = RedditAdapter(client)

        # When
        page = await adapter.fetch_page(
            RedditFetchRequest(
                preflight=reddit_context(reddit_authorization()),
                credentials=reddit_credentials(),
                cursor=None,
                accepted_so_far=19,
                page_size=20,
            )
        )

    # Then
    assert requests[0].url.params["limit"] == "1"
    assert page.accepted_count == 1
    assert page.termination is PageTermination.REVIEWED_POST_CAP
    assert page.next_cursor == "t3_cap_cursor"


@pytest.mark.asyncio
async def test_successful_page_exposes_adaptive_rate_limit_pause(
    reddit_page_response: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    # Given
    def handler(request: httpx2.Request) -> httpx2.Response:
        response = reddit_page_response(request)
        response.headers["x-ratelimit-remaining"] = "0"
        response.headers["x-ratelimit-reset"] = "12.5"
        return response

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        adapter = RedditAdapter(client)

        # When
        page = await adapter.fetch_page(
            RedditFetchRequest(
                preflight=reddit_context(reddit_authorization()),
                credentials=reddit_credentials(),
                cursor=None,
                accepted_so_far=0,
                page_size=2,
            )
        )

    # Then
    assert page.rate_limit.remaining == Decimal(0)
    assert page.rate_limit.retry_after_seconds == Decimal("12.5")
    assert page.termination is PageTermination.RATE_LIMIT_PAUSE
    assert page.next_cursor == "t3_after_page_1"


@pytest.mark.asyncio
async def test_429_is_typed_quota_without_response_body_or_token() -> None:
    # Given
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429,
            text="tests-only-private-author tests-only-oauth-token",
            headers={"retry-after": "17"},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        adapter = RedditAdapter(client)
        request = RedditFetchRequest(
            preflight=reddit_context(reddit_authorization()),
            credentials=reddit_credentials(),
            cursor=None,
            accepted_so_far=0,
            page_size=2,
        )

        # When / Then
        with pytest.raises(AdapterHttpError) as raised:
            _ = await adapter.fetch_page(request)

    assert raised.value.classification.kind is HttpFailureKind.QUOTA
    assert raised.value.classification.retry_after_seconds == Decimal(17)
    assert "private-author" not in str(raised.value)
    assert "oauth-token" not in str(raised.value)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (403, HttpFailureKind.POLICY),
        (404, HttpFailureKind.TERMINAL),
        (503, HttpFailureKind.RETRYABLE),
        (429, HttpFailureKind.QUOTA),
    ],
)
def test_http_failures_are_classified_exhaustively(
    status: int, expected: HttpFailureKind
) -> None:
    # Given / When
    result = classify_http_failure(HttpFailure(status_code=status))

    # Then
    assert result.kind is expected


def test_retryable_backoff_matches_bounded_exponential_parameters() -> None:
    # Given / When
    result = classify_http_failure(HttpFailure(status_code=503))

    # Then
    assert result.backoff is not None
    assert result.backoff.delay_seconds(1, Decimal(1)) == Decimal(45)
    assert result.backoff.delay_seconds(2, Decimal(1)) == Decimal(150)
    assert result.backoff.max_attempts == 3
