"""Deterministic page request, item disposition, and chain calculations."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, assert_never

from app.domain.enums import PageItemDisposition

from .base import canonical_json_hash, format_utc
from .normalizer import NormalizedPost, OversizeRejection
from .page_models import PageItemPlan

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from app.domain.types import JsonValue

    from .normalizer import NormalizedPageItem
    from .page_models import ExistingPostVersion, PageCommitRequest


def page_chain_link(previous_hash: str, page_content_hash: str) -> str:
    """Bind one page-result digest to the previous raw chain link."""
    return sha256(
        b"page-chain-link/v1\n"
        + bytes.fromhex(previous_hash)
        + bytes.fromhex(page_content_hash)
    ).hexdigest()


def page_request_hash(
    request: PageCommitRequest,
    items: tuple[NormalizedPageItem, ...],
) -> str:
    """Hash normalized request facts while excluding the idempotency key."""
    values: list[JsonValue] = []
    for item in items:
        match item:
            case NormalizedPost():
                values.append(
                    {
                        "body": item.body,
                        "canonical_url": item.canonical_url,
                        "comments_count": item.comments_count,
                        "content_hash": item.content_hash,
                        "language": item.language,
                        "published_at": format_utc(item.published_at),
                        "source_post_id": item.source_post_id,
                        "title": item.title,
                        "upvote_or_score": item.upvote_or_score,
                    }
                )
                continue
            case OversizeRejection():
                values.append(
                    {
                        "body_bytes": item.body_bytes,
                        "canonical_url": item.canonical_url,
                        "content_hash": item.content_hash,
                        "rejection_reason": item.rejection_reason,
                        "source_post_id": item.source_post_id,
                    }
                )
                continue
            case _:
                assert_never(item)
    payload: JsonValue = {
        "attempt": request.attempt,
        "command_id": str(request.command_id),
        "expected_checkpoint_revision": request.expected_checkpoint_revision,
        "expected_cursor": request.expected_cursor,
        "is_terminal_page": request.is_terminal_page,
        "next_cursor": request.next_cursor,
        "page_fetch_finished_at": format_utc(request.page_fetch_finished_at),
        "page_fetch_started_at": format_utc(request.page_fetch_started_at),
        "page_ordinal": request.page_ordinal,
        "posts": values,
        "schema": "page-request/v1",
        "source_page_item_count": request.source_page_item_count,
        "source_page_receipt_sha256": request.source_page_receipt_sha256,
        "terminal_reason": (
            request.terminal_reason.value if request.terminal_reason else None
        ),
    }
    return canonical_json_hash(payload)


def classify_page_items(
    normalized: tuple[NormalizedPageItem, ...],
    existing: tuple[ExistingPostVersion, ...],
    new_id: Callable[[], UUID],
) -> tuple[PageItemPlan, ...]:
    """Classify ordered normalized items against locked current revisions."""
    plans: list[PageItemPlan] = []
    for ordinal, item in enumerate(normalized):
        match item:
            case OversizeRejection():
                plans.append(
                    PageItemPlan(
                        ordinal,
                        PageItemDisposition.REJECTED_OVERSIZE,
                        item,
                        item.content_hash,
                        None,
                        None,
                        None,
                    )
                )
                continue
            case NormalizedPost():
                current = next(
                    (
                        row
                        for row in existing
                        if row.source_post_id == item.source_post_id
                    ),
                    None,
                )
                if (
                    current is not None
                    and current.current_content_hash == item.content_hash
                ):
                    plans.append(
                        PageItemPlan(
                            ordinal,
                            PageItemDisposition.DUPLICATE,
                            item,
                            item.content_hash,
                            current.post_id,
                            current.current_version_id,
                            current.current_revision,
                        )
                    )
                else:
                    post_id = current.post_id if current is not None else new_id()
                    revision = (
                        current.current_revision + 1 if current is not None else 1
                    )
                    plans.append(
                        PageItemPlan(
                            ordinal,
                            PageItemDisposition.ACCEPTED,
                            item,
                            item.content_hash,
                            post_id,
                            new_id(),
                            revision,
                        )
                    )
                continue
            case _:
                assert_never(item)
    return tuple(plans)
