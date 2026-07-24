"""Atomic page-CAS planning, idempotent replay, and hash-chain binding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.enums import PageItemDisposition
from app.services.configuration.canonical import canonical_bytes

from .base import (
    CollectionError,
    CollectionErrorCode,
    canonical_json_hash,
    hash_token,
)
from .normalizer import normalize_page_items
from .page_item_planning import (
    classify_page_items,
    page_chain_link,
    page_request_hash,
)
from .page_models import (
    ExistingPostVersion,
    PageCommitContext,
    PageCommitOutcome,
    PageCommitPlan,
    PageCommitRecord,
    PageCommitRequest,
    PageCommitResponse,
    PageItemPlan,
)
from .page_transitions import (
    advance_checkpoint,
    advance_run,
    server_terminal_request,
    validate_new_page_commit,
    validate_terminal_cap,
)

__all__ = (
    "ExistingPostVersion",
    "PageCommitContext",
    "PageCommitOutcome",
    "PageCommitPlan",
    "PageCommitRecord",
    "PageCommitRequest",
    "PageCommitResponse",
    "PageItemPlan",
    "page_chain_link",
    "prepare_page_commit",
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from app.domain.types import JsonValue


def prepare_page_commit(
    context: PageCommitContext,
    request: PageCommitRequest,
    new_id: Callable[[], UUID],
) -> PageCommitPlan:
    """Validate and plan one page transaction without performing persistence."""
    request = server_terminal_request(context, request)
    normalized = normalize_page_items(request.posts)
    request_hash = page_request_hash(request, normalized)
    run = context.run
    ownership = request.command_id == run.command_id and request.attempt == run.attempt
    if not ownership:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    replay = context.existing_idempotency_commit
    if replay is not None:
        if replay.page_request_hash != request_hash:
            raise CollectionError(CollectionErrorCode.IDEMPOTENCY_KEY_REUSED, 409)
        if replay.lease_identity_hash != hash_token(request.lease_token):
            raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
        response = PageCommitResponse.model_validate_json(replay.stored_response)
        return PageCommitPlan(
            commit=replay,
            items=(),
            updated_checkpoint=context.checkpoint,
            updated_run=run,
            outcome=PageCommitOutcome(200, response, replay.stored_response),
            should_persist=False,
        )
    validate_new_page_commit(context, request)
    items = classify_page_items(normalized, context.existing_posts, new_id)
    accepted = sum(item.disposition is PageItemDisposition.ACCEPTED for item in items)
    duplicates = sum(
        item.disposition is PageItemDisposition.DUPLICATE for item in items
    )
    rejected = sum(
        item.disposition is PageItemDisposition.REJECTED_OVERSIZE for item in items
    )
    validate_terminal_cap(context, request, accepted)
    commit_id = new_id()
    next_revision = context.checkpoint.revision + 1
    result_values: list[JsonValue] = [
        {
            "disposition": item.disposition.value,
            "item_ordinal": item.item_ordinal,
            "normalized_content_hash": item.normalized_content_hash,
            "post_id": str(item.post_id) if item.post_id else None,
            "post_version_id": str(item.post_version_id)
            if item.post_version_id
            else None,
            "revision": item.revision,
            "source_post_id": item.normalized_item.source_post_id,
        }
        for item in items
    ]
    content_hash = canonical_json_hash(
        {
            "accepted_count": accepted,
            "checkpoint_revision": next_revision,
            "duplicate_count": duplicates,
            "is_terminal_page": request.is_terminal_page,
            "items": result_values,
            "next_cursor": request.next_cursor,
            "page_ordinal": request.page_ordinal,
            "page_request_hash": request_hash,
            "rejected_count": rejected,
            "schema": "page-result/v1",
            "terminal_reason": request.terminal_reason.value
            if request.terminal_reason
            else None,
        }
    )
    chain = page_chain_link(run.committed_page_hash_chain, content_hash)
    response = PageCommitResponse(
        page_commit_id=commit_id,
        checkpoint_revision=next_revision,
        next_cursor=request.next_cursor,
        accepted_count=accepted,
        duplicate_count=duplicates,
        rejected_count=rejected,
        page_content_hash=content_hash,
    )
    response_bytes = canonical_bytes(response)
    commit = PageCommitRecord(
        commit_id,
        run.id,
        run.command_id,
        run.attempt,
        run.lease_identity_hash,
        request.page_idempotency_key,
        request.page_ordinal,
        request.expected_checkpoint_revision,
        next_revision,
        request.expected_cursor,
        request.next_cursor,
        request_hash,
        content_hash,
        run.committed_page_hash_chain,
        chain,
        request.source_page_receipt_sha256,
        request.source_page_item_count,
        accepted,
        duplicates,
        rejected,
        request.is_terminal_page,
        request.terminal_reason,
        response_bytes,
    )
    checkpoint = advance_checkpoint(context.checkpoint, request, normalized)
    updated_run = advance_run(run, commit, context.db_now)
    return PageCommitPlan(
        commit=commit,
        items=items,
        updated_checkpoint=checkpoint,
        updated_run=updated_run,
        outcome=PageCommitOutcome(201, response, response_bytes),
        should_persist=True,
    )
