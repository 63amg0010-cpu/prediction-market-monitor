from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import httpx2
import pytest
from app.collection.adapters.models import NormalizedPost, RejectedOversize
from app.collection.adapters.reddit import RedditAdapter, RedditFetchRequest
from app.collection.normalizer import compute_content_hash

from .factories import reddit_authorization, reddit_context, reddit_credentials

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.asyncio
async def test_normalization_retains_full_original_and_strips_author_and_raw(
    caplog: pytest.LogCaptureFixture,
    reddit_page_response: Callable[[httpx2.Request], httpx2.Response],
) -> None:
    # Given
    caplog.set_level(logging.DEBUG)
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(reddit_page_response)
    ) as client:
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
    first, second = page.items
    assert isinstance(first, NormalizedPost)
    assert first.title == "Full prediction-market title"
    assert first.body == "Full body line one.\nFull body line two."
    assert first.canonical_url == (
        "https://www.reddit.com/r/Polymarket/comments/alpha123/"
        "full_prediction_market_title/"
    )
    assert first.comments_count == 12
    assert first.upvote_or_score == 34
    assert first.content_hash == compute_content_hash(first.title, first.body)
    assert isinstance(second, NormalizedPost)
    assert second.comments_count is None
    assert second.upvote_or_score is None
    serialized = "".join(item.model_dump_json() for item in page.items)
    assert "author" not in serialized
    assert "raw" not in serialized
    assert "tests-only-private-author" not in caplog.text


@pytest.mark.asyncio
async def test_oversize_input_is_rejected_wholesale() -> None:
    # Given
    oversized_body = "가" * 90_000
    payload = {
        "kind": "Listing",
        "data": {
            "after": None,
            "children": [
                {
                    "kind": "t3",
                    "data": {
                        "id": "oversize1",
                        "subreddit": "Polymarket",
                        "title": "Oversize title",
                        "selftext": oversized_body,
                        "permalink": "/r/Polymarket/comments/oversize1/oversize/",
                        "created_utc": 1784570400.0,
                        "num_comments": 1,
                        "score": 1,
                        "author": "tests-only-oversize-author",
                    },
                }
            ],
        },
    }

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            content=json.dumps(payload, ensure_ascii=False).encode(),
            headers={"content-type": "application/json"},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        adapter = RedditAdapter(client)

        # When
        page = await adapter.fetch_page(
            RedditFetchRequest(
                preflight=reddit_context(reddit_authorization()),
                credentials=reddit_credentials(),
                cursor=None,
                accepted_so_far=0,
                page_size=1,
            )
        )

    # Then
    rejected = page.items[0]
    assert isinstance(rejected, RejectedOversize)
    assert rejected.size_bytes > 262_144
    assert rejected.reason == "rejected_oversize"
    assert not hasattr(rejected, "title")
    assert not hasattr(rejected, "body")
    serialized = rejected.model_dump_json()
    assert oversized_body[:100] not in serialized
    assert "author" not in serialized
