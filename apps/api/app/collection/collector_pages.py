"""Collector adapter-page conversion and commit request construction."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid5

from pydantic import AnyHttpUrl

from app.domain.enums import TerminalReason
from app.services.configuration.canonical import canonical_sha256

from .adapters.models import (
    AdapterPage,
    PageTermination,
    RejectedOversize,
)
from .adapters.models import (
    NormalizedPost as AdapterNormalizedPost,
)
from .normalizer import AcceptedPostInput, OversizePostInput, PagePostInput
from .page_commit import PageCommitRequest

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from .collector_contracts import PageCursor


def page_request(  # noqa: PLR0913 - exact page boundary facts.
    command_id: UUID,
    attempt: int,
    lease_token: str,
    run_id: UUID,
    state: PageCursor,
    page: AdapterPage,
    started_at: datetime,
    finished_at: datetime,
) -> PageCommitRequest:
    """Build an idempotent commit request from one bounded adapter page."""
    terminal_reason = _terminal_reason(page.termination)
    return PageCommitRequest(
        command_id=command_id,
        attempt=attempt,
        lease_token=lease_token,
        page_idempotency_key=uuid5(
            run_id, f"{attempt}:{state.ordinal}:{state.revision}:{state.cursor!r}"
        ),
        expected_checkpoint_revision=state.revision,
        expected_cursor=state.cursor,
        next_cursor=page.next_cursor,
        page_ordinal=state.ordinal,
        posts=tuple(_page_item(item) for item in page.items),
        source_page_item_count=len(page.items),
        source_page_receipt_sha256=canonical_sha256(page),
        page_fetch_started_at=started_at,
        page_fetch_finished_at=finished_at,
        is_terminal_page=terminal_reason is not None,
        terminal_reason=terminal_reason,
    )


def _page_item(item: AdapterNormalizedPost | RejectedOversize) -> PagePostInput:
    if isinstance(item, AdapterNormalizedPost):
        return AcceptedPostInput(
            source_post_id=item.source_post_id,
            canonical_url=AnyHttpUrl(item.canonical_url),
            title=item.title,
            body=item.body,
            published_at=item.published_at,
            language=item.language,
            comments_count=item.comments_count,
            upvote_or_score=item.upvote_or_score,
            content_hash=item.content_hash,
        )
    return OversizePostInput(
        source_post_id=item.source_post_id,
        canonical_url=AnyHttpUrl(item.canonical_url),
        content_hash=item.content_hash,
        body_bytes=item.size_bytes,
        rejection_reason="rejected_oversize",
    )


def _terminal_reason(termination: PageTermination) -> TerminalReason | None:
    if termination is PageTermination.SOURCE_EXHAUSTED:
        return TerminalReason.SOURCE_EXHAUSTED
    if termination is PageTermination.REVIEWED_POST_CAP:
        return TerminalReason.REVIEWED_POST_CAP
    return None


__all__ = ("page_request",)
