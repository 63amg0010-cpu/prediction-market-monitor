from datetime import UTC, datetime

import pytest
from app.collection.base import MAX_POST_BYTES, CollectionError, CollectionErrorCode
from app.collection.normalizer import (
    AcceptedPostInput,
    NormalizedPost,
    OversizeRejection,
    compute_content_hash,
    normalize_page_items,
)
from pydantic import AnyHttpUrl, ValidationError

PUBLISHED = datetime(2026, 7, 20, tzinfo=UTC)


def post(body: str, source_post_id: str = "post-1") -> AcceptedPostInput:
    title = "title"
    return AcceptedPostInput(
        source_post_id=source_post_id,
        canonical_url=AnyHttpUrl("https://example.com/post-1"),
        title=title,
        body=body,
        published_at=PUBLISHED,
        language="en",
        comments_count=None,
        upvote_or_score=None,
        content_hash=compute_content_hash(title, body),
    )


@pytest.mark.parametrize("field", ["author", "raw_payload"])
def test_author_and_raw_provider_fields_are_rejected(field: str) -> None:
    # Given: an otherwise valid normalized post with a forbidden provider field.
    raw = post("body").model_dump(mode="json")
    raw[field] = "must-not-cross-boundary"

    # When/Then: the Pydantic trust boundary rejects the complete item.
    with pytest.raises(ValidationError):
        _ = AcceptedPostInput.model_validate(raw)


def test_oversize_body_is_wholly_rejected_at_utf8_boundary() -> None:
    # Given: one item exactly at 256 KiB and one byte beyond it.
    boundary = post("a" * MAX_POST_BYTES)
    oversize = post("a" * (MAX_POST_BYTES + 1), "post-2")

    # When: the server normalizes both items.
    normalized = normalize_page_items((boundary, oversize))

    # Then: the boundary is retained but the oversize result has no title/body.
    assert isinstance(normalized[0], NormalizedPost)
    assert normalized[0].body == boundary.body
    assert isinstance(normalized[1], OversizeRejection)
    assert normalized[1].body_bytes == MAX_POST_BYTES + 1
    assert not hasattr(normalized[1], "body")


def test_client_content_hash_cannot_replace_server_hash() -> None:
    # Given: normalized content paired with a fabricated digest.
    item = post("body").model_copy(update={"content_hash": "0" * 64})

    # When/Then: the server recomputation rejects the mismatch.
    with pytest.raises(CollectionError) as captured:
        _ = normalize_page_items((item,))
    assert captured.value.code is CollectionErrorCode.INVALID_CONTRACT
