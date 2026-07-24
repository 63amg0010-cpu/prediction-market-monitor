"""Deterministic in-memory Phase 2 boundary fixtures.

These fixtures model the rows locked by the page and completion services.  The
tests still call the production planners; the fixture only supplies persisted
facts, never expected responses.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from app.collection.authorization import AuthorizationSnapshot
from app.collection.base import MAX_POST_BYTES, hash_token
from app.collection.checkpoint import CheckpointState, RunStart, RunState, start_run
from app.collection.commands import CommandState
from app.collection.normalizer import (
    AcceptedPostInput,
    OversizePostInput,
    compute_content_hash,
)
from app.collection.page_commit import (
    ExistingPostVersion,
    PageCommitContext,
    PageCommitRequest,
)
from app.domain.enums import (
    AuthorizationStatus,
    CommandKind,
    CommandStatus,
    TerminalReason,
)
from pydantic import AnyHttpUrl

NOW: Final = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
LEASE: Final = "l" * 43
SOURCE_ID: Final = UUID("0c90e846-67f0-4fa8-9a22-eb2e226faab5")
COMMAND_ID: Final = UUID("826936f4-9eae-43f4-aa16-68955681cb88")
RUN_ID: Final = UUID("7c4ade1f-b450-46b2-aaed-cda121160d1e")
CHECKPOINT_ID: Final = UUID("12ef0ae3-1b76-4f49-a62a-e4da32b08084")
AUTHORIZATION_ID: Final = UUID("5a20af03-f8b3-45a4-9f31-9a4a79fb390d")
IDEMPOTENCY_KEY: Final = UUID("fb0959b9-4d1d-4e8a-b1dd-fe29b47d8e72")


@dataclass(frozen=True, slots=True)
class PageRequestOverrides:
    """Semantic differences used to construct one page request."""

    expected_revision: int = 0
    expected_cursor: str | None = None
    next_cursor: str | None = "cursor-1"
    ordinal: int = 0
    page_idempotency_key: UUID = IDEMPOTENCY_KEY
    terminal_reason: TerminalReason | None = TerminalReason.SOURCE_EXHAUSTED
    posts: tuple[AcceptedPostInput | OversizePostInput, ...] = ()


@dataclass(frozen=True, slots=True)
class PageContextOverrides:
    """Locked-row substitutions for a page context fixture."""

    source_id: UUID = SOURCE_ID
    command_id: UUID = COMMAND_ID
    run_id: UUID = RUN_ID
    source_ids: tuple[UUID, ...] = (SOURCE_ID,)
    authorization: AuthorizationSnapshot | None = None
    checkpoint: CheckpointState | None = None
    run: RunState | None = None
    existing_posts: tuple[ExistingPostVersion, ...] = ()
    reviewed_page_cap: int = 4
    reviewed_post_cap: int = 20


def page_context(overrides: PageContextOverrides | None = None) -> PageCommitContext:
    """Build one immutable locked-row snapshot for page planning."""
    values = overrides or PageContextOverrides()
    current_checkpoint = values.checkpoint or CheckpointState(
        CHECKPOINT_ID,
        values.source_id,
        "scope-v1",
        0,
        None,
    )
    command = CommandState(
        values.command_id,
        "scope-v1",
        values.source_ids,
        CommandKind.SCHEDULED,
        CommandStatus.RUNNING,
        1,
        NOW,
        claimed_at=NOW,
        heartbeat_at=NOW,
        lease_hash=hash_token(LEASE),
    )
    current_run = values.run or start_run(
        RunStart(
            values.run_id,
            values.command_id,
            values.source_id,
            "scope-v1",
            1,
            hash_token(LEASE),
            NOW,
        ),
        current_checkpoint,
    )
    current_authorization = values.authorization or AuthorizationSnapshot(
        decision_id=AUTHORIZATION_ID,
        source_id=values.source_id,
        scope_version="scope-v1",
        enabled=True,
        status=AuthorizationStatus.APPROVED,
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        revoked_at=None,
    )
    if current_run.authorization_decision_id is None:
        current_run = replace(
            current_run,
            authorization_decision_id=current_authorization.decision_id,
            reviewed_page_cap=values.reviewed_page_cap,
            reviewed_post_cap=values.reviewed_post_cap,
        )
    return PageCommitContext(
        db_now=NOW,
        command=command,
        run=current_run,
        checkpoint=current_checkpoint,
        authorization=current_authorization,
        existing_posts=values.existing_posts,
        existing_idempotency_commit=None,
        existing_ordinal_commit=None,
        reviewed_page_cap=values.reviewed_page_cap,
        reviewed_post_cap=values.reviewed_post_cap,
    )


def page_request(
    context: PageCommitContext,
    overrides: PageRequestOverrides | None = None,
) -> PageCommitRequest:
    """Build a valid request from the fixture's checkpoint snapshot."""
    values = overrides or PageRequestOverrides()
    return PageCommitRequest(
        command_id=context.command.id,
        attempt=context.run.attempt,
        lease_token=LEASE,
        page_idempotency_key=values.page_idempotency_key,
        expected_checkpoint_revision=values.expected_revision,
        expected_cursor=values.expected_cursor,
        next_cursor=values.next_cursor,
        page_ordinal=values.ordinal,
        posts=values.posts,
        source_page_item_count=len(values.posts),
        source_page_receipt_sha256="a" * 64,
        page_fetch_started_at=NOW,
        page_fetch_finished_at=NOW + timedelta(seconds=1),
        is_terminal_page=values.terminal_reason is not None,
        terminal_reason=values.terminal_reason,
    )


def accepted_post(
    source_post_id: str = "post-1",
    *,
    title: str = "Prediction market",
    body: str = "Community reaction",
    published_at: datetime = NOW,
) -> AcceptedPostInput:
    """Return an author-free accepted item with an independently derived hash."""
    return AcceptedPostInput(
        source_post_id=source_post_id,
        canonical_url=AnyHttpUrl(f"https://example.test/{source_post_id}"),
        title=title,
        body=body,
        published_at=published_at,
        language="en",
        comments_count=None,
        upvote_or_score=None,
        content_hash=compute_content_hash(title, body),
    )


def oversize_post(source_post_id: str = "oversize-1") -> OversizePostInput:
    """Return a descriptor-only item that cannot produce a persisted version."""
    return OversizePostInput(
        source_post_id=source_post_id,
        canonical_url=AnyHttpUrl(f"https://example.test/{source_post_id}"),
        content_hash="c" * 64,
        body_bytes=MAX_POST_BYTES + 1,
        rejection_reason="rejected_oversize",
    )
