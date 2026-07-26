"""Author-free normalization for parsed Reddit post payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.collection.normalizer import compute_body_bytes, compute_content_hash
from app.domain.enums import SourcePlatform

from .http_errors import HttpFailureKind
from .models import NormalizedItem, NormalizedPost, RejectedOversize
from .reddit_contracts import ALLOWED_SUBREDDITS, MAX_CONTENT_BYTES
from .reddit_http import provider_response_error

if TYPE_CHECKING:
    from .reddit_models import RedditPostPayload


def normalize_reddit_post(raw: RedditPostPayload) -> NormalizedItem:
    """Return an author-free accepted post or an oversize rejection."""
    if raw.subreddit not in ALLOWED_SUBREDDITS:
        raise provider_response_error(
            HttpFailureKind.POLICY,
            "subreddit_scope_violation",
        )
    prefix = f"/r/{raw.subreddit}/comments/{raw.id}/"
    if (
        not raw.permalink.startswith(prefix)
        or "?" in raw.permalink
        or "#" in raw.permalink
    ):
        raise provider_response_error(
            HttpFailureKind.TERMINAL,
            "canonical_url_invalid",
        )
    canonical_url = f"https://www.reddit.com{raw.permalink}"
    content_hash = compute_content_hash(raw.title, raw.selftext)
    size_bytes = compute_body_bytes(raw.selftext)
    if size_bytes > MAX_CONTENT_BYTES:
        return RejectedOversize(
            source=SourcePlatform.REDDIT,
            source_post_id=raw.id,
            canonical_url=canonical_url,
            content_hash=content_hash,
            size_bytes=size_bytes,
        )
    try:
        published_at = datetime.fromtimestamp(raw.created_utc, tz=UTC)
    except (OSError, OverflowError, ValueError) as error:
        raise provider_response_error(
            HttpFailureKind.TERMINAL,
            "published_timestamp_invalid",
        ) from error
    return NormalizedPost(
        source=SourcePlatform.REDDIT,
        source_post_id=raw.id,
        canonical_url=canonical_url,
        title=raw.title,
        body=raw.selftext,
        published_at=published_at,
        language="en",
        comments_count=raw.num_comments,
        upvote_or_score=raw.score,
        content_hash=content_hash,
        size_bytes=size_bytes,
    )
