"""Fail-closed page CAS validation and checkpoint/run transitions."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, assert_never

from app.domain.enums import CommandStatus, RunStatus, TerminalReason

from .authorization import require_active_authorization
from .base import (
    CollectionError,
    CollectionErrorCode,
    hash_token,
    token_matches,
)
from .normalizer import NormalizedPost, OversizeRejection

if TYPE_CHECKING:
    from datetime import datetime

    from .checkpoint import CheckpointState, RunState
    from .normalizer import NormalizedPageItem
    from .page_models import PageCommitContext, PageCommitRecord, PageCommitRequest


def validate_new_page_commit(
    context: PageCommitContext,
    request: PageCommitRequest,
) -> None:
    """Validate authorization, lease ownership, CAS, ordinal, and stream state."""
    authorization = require_active_authorization(
        context.authorization,
        context.run.source_id,
        context.run.scope_version,
        context.db_now,
    )
    if authorization.decision_id != context.run.authorization_decision_id:
        raise CollectionError(
            CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE,
            403,
        )
    valid_lease = (
        token_matches(request.lease_token, context.command.lease_hash)
        and hash_token(request.lease_token) == context.run.lease_identity_hash
    )
    if (
        context.command.status is not CommandStatus.RUNNING
        or context.run.status is not RunStatus.RUNNING
        or not valid_lease
    ):
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    if context.existing_ordinal_commit is not None:
        raise CollectionError(
            CollectionErrorCode.ORDINAL_ALREADY_COMMITTED,
            409,
            existing_commit_id=context.existing_ordinal_commit.id,
        )
    if context.run.terminal_page_commit_id is not None:
        raise CollectionError(
            CollectionErrorCode.RUN_STREAM_SEALED,
            409,
            existing_commit_id=context.run.terminal_page_commit_id,
        )
    reviewed_page_cap, _ = _reviewed_caps(context.run)
    if context.run.committed_page_count >= reviewed_page_cap:
        raise CollectionError(CollectionErrorCode.RUN_STREAM_SEALED, 409)
    if (
        request.expected_checkpoint_revision != context.checkpoint.revision
        or request.expected_cursor != context.checkpoint.cursor
    ):
        raise CollectionError(
            CollectionErrorCode.CHECKPOINT_CONFLICT,
            409,
            context.checkpoint.revision,
            context.checkpoint.cursor,
        )
    if request.page_ordinal != context.run.next_page_ordinal:
        raise CollectionError(
            CollectionErrorCode.ORDINAL_GAP,
            409,
            expected_page_ordinal=context.run.next_page_ordinal,
        )


def validate_terminal_cap(
    context: PageCommitContext,
    request: PageCommitRequest,
    accepted: int,
) -> None:
    """Reject a reviewed cap marker until its server-derived threshold is met."""
    reviewed_page_cap, reviewed_post_cap = _reviewed_caps(context.run)
    match request.terminal_reason:
        case TerminalReason.REVIEWED_PAGE_CAP:
            valid = context.run.committed_page_count + 1 == reviewed_page_cap
        case TerminalReason.REVIEWED_POST_CAP:
            valid = context.run.accepted_count + accepted >= reviewed_post_cap
        case TerminalReason.SOURCE_EXHAUSTED | None:
            return
        case _:
            assert_never(request.terminal_reason)
    if not valid:
        raise CollectionError(CollectionErrorCode.INVALID_TERMINAL_REASON, 422)


def server_terminal_request(
    context: PageCommitContext,
    request: PageCommitRequest,
) -> PageCommitRequest:
    """Derive an exact immutable page-cap marker from the claimed run snapshot."""
    reviewed_page_cap, _ = _reviewed_caps(context.run)
    reaches_cap = context.run.committed_page_count + 1 == reviewed_page_cap
    if reaches_cap and request.terminal_reason is None:
        return request.model_copy(
            update={
                "is_terminal_page": True,
                "terminal_reason": TerminalReason.REVIEWED_PAGE_CAP,
            }
        )
    return request


def _reviewed_caps(run: RunState) -> tuple[int, int]:
    page_cap = run.reviewed_page_cap
    post_cap = run.reviewed_post_cap
    if page_cap is None or post_cap is None or page_cap < 1 or post_cap < 1:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    return page_cap, post_cap


def advance_checkpoint(
    checkpoint: CheckpointState,
    request: PageCommitRequest,
    items: tuple[NormalizedPageItem, ...],
) -> CheckpointState:
    """Advance cursor, revision, and watermark from normalized accepted items."""
    watermark = (checkpoint.watermark_published_at, checkpoint.watermark_source_post_id)
    candidates: list[tuple[datetime, str]] = []
    for item in items:
        match item:
            case NormalizedPost():
                candidates.append((item.published_at, item.source_post_id))
                continue
            case OversizeRejection():
                continue
            case _:
                assert_never(item)
    if candidates:
        candidate = max(candidates)
        if watermark[0] is None or candidate > watermark:
            watermark = candidate
    return replace(
        checkpoint,
        revision=checkpoint.revision + 1,
        cursor=request.next_cursor,
        watermark_published_at=watermark[0],
        watermark_source_post_id=watermark[1],
    )


def advance_run(
    run: RunState,
    commit: PageCommitRecord,
    db_now: datetime,
) -> RunState:
    """Advance aggregate run facts and seal terminal marker facts together."""
    updated = replace(
        run,
        accepted_count=run.accepted_count + commit.accepted_count,
        committed_page_count=run.committed_page_count + 1,
        committed_page_hash_chain=commit.resulting_chain_hash,
        duplicate_count=run.duplicate_count + commit.duplicate_count,
        final_cursor=commit.next_cursor,
        final_page_ordinal=commit.page_ordinal,
        last_page_commit_id=commit.id,
        next_page_ordinal=commit.page_ordinal + 1,
        rejected_count=run.rejected_count + commit.rejected_count,
    )
    if not commit.is_terminal_page:
        return updated
    return replace(
        updated,
        terminal_page_commit_id=commit.id,
        terminal_page_ordinal=commit.page_ordinal,
        terminal_cursor=commit.next_cursor,
        terminal_reason=commit.terminal_reason,
        terminal_chain_hash=commit.resulting_chain_hash,
        completion_ready_at=db_now,
    )
