from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.collection.authorization import AuthorizationSnapshot
from app.collection.base import CollectionError, CollectionErrorCode, hash_token
from app.collection.checkpoint import CheckpointState, RunStart, start_run
from app.collection.commands import CommandState
from app.collection.normalizer import AcceptedPostInput, compute_content_hash
from app.collection.page_commit import (
    PageCommitContext,
    PageCommitRequest,
    prepare_page_commit,
)
from app.domain.enums import (
    AuthorizationStatus,
    CommandKind,
    CommandStatus,
    RunStatus,
    TerminalReason,
)
from pydantic import AnyHttpUrl

NOW = datetime(2026, 7, 20, tzinfo=UTC)
LEASE = "l" * 43


class IdSequence:
    def __init__(self) -> None:
        self._next: int = 100

    def __call__(self) -> UUID:
        result = UUID(int=self._next)
        self._next += 1
        return result


def context() -> PageCommitContext:
    source_id = UUID(int=1)
    command_id = UUID(int=2)
    checkpoint = CheckpointState(UUID(int=3), source_id, "scope-v1", 0, None)
    command = CommandState(
        command_id,
        "scope-v1",
        (source_id,),
        CommandKind.SCHEDULED,
        CommandStatus.RUNNING,
        1,
        NOW,
        claimed_at=NOW,
        heartbeat_at=NOW,
        lease_hash=hash_token(LEASE),
    )
    run = start_run(
        RunStart(
            UUID(int=4),
            command_id,
            source_id,
            "scope-v1",
            1,
            hash_token(LEASE),
            NOW,
        ),
        checkpoint,
    )
    assert run.status is RunStatus.RUNNING
    authorization = AuthorizationSnapshot(
        decision_id=UUID(int=5),
        source_id=source_id,
        scope_version="scope-v1",
        enabled=True,
        status=AuthorizationStatus.APPROVED,
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        revoked_at=None,
    )
    run = replace(
        run,
        authorization_decision_id=authorization.decision_id,
        reviewed_page_cap=4,
        reviewed_post_cap=20,
    )
    return PageCommitContext(
        db_now=NOW,
        command=command,
        run=run,
        checkpoint=checkpoint,
        authorization=authorization,
        existing_posts=(),
        existing_idempotency_commit=None,
        existing_ordinal_commit=None,
        reviewed_page_cap=4,
        reviewed_post_cap=20,
    )


def request(
    ctx: PageCommitContext,
    *,
    terminal: bool = False,
    reason: TerminalReason | None = None,
    posts: tuple[AcceptedPostInput, ...] = (),
) -> PageCommitRequest:
    return PageCommitRequest(
        command_id=ctx.command.id,
        attempt=1,
        lease_token=LEASE,
        page_idempotency_key=UUID(int=10),
        expected_checkpoint_revision=0,
        expected_cursor=None,
        next_cursor="cursor-1",
        page_ordinal=0,
        posts=posts,
        source_page_item_count=len(posts),
        source_page_receipt_sha256="a" * 64,
        page_fetch_started_at=NOW,
        page_fetch_finished_at=NOW + timedelta(seconds=1),
        is_terminal_page=terminal,
        terminal_reason=reason,
    )


def test_terminal_commit_seals_cursor_ordinal_and_chain() -> None:
    # Given: a running source whose first successful fetch is empty and exhausted.
    ctx = context()
    page = request(
        ctx,
        terminal=True,
        reason=TerminalReason.SOURCE_EXHAUSTED,
    )

    # When: the server prepares its atomic commit.
    plan = prepare_page_commit(ctx, page, IdSequence())

    # Then: one empty terminal commit advances CAS and seals all terminal facts.
    assert plan.outcome.status_code == 201
    assert plan.updated_checkpoint.revision == 1
    assert plan.updated_run.terminal_page_commit_id == plan.commit.id
    assert plan.updated_run.terminal_page_ordinal == 0
    assert plan.updated_run.terminal_cursor == "cursor-1"
    assert plan.updated_run.terminal_chain_hash == plan.commit.resulting_chain_hash
    assert plan.updated_run.status is RunStatus.RUNNING


def test_page_content_and_chain_change_when_terminal_flag_changes() -> None:
    # Given: otherwise identical nonterminal and terminal requests.
    ctx = context()
    nonterminal = request(ctx)
    terminal = request(
        ctx,
        terminal=True,
        reason=TerminalReason.SOURCE_EXHAUSTED,
    )

    # When: each result is hashed from persisted semantics.
    first = prepare_page_commit(ctx, nonterminal, IdSequence())
    second = prepare_page_commit(ctx, terminal, IdSequence())

    # Then: the content hash and chain cryptographically bind terminal evidence.
    assert first.commit.page_content_hash != second.commit.page_content_hash
    assert first.commit.resulting_chain_hash != second.commit.resulting_chain_hash


def test_premature_reviewed_cap_terminal_reason_is_rejected() -> None:
    # Given: the first page claims the reviewed four-page cap was reached.
    ctx = context()
    page = request(
        ctx,
        terminal=True,
        reason=TerminalReason.REVIEWED_PAGE_CAP,
    )

    # When/Then: server-observed counters reject the fabricated terminal reason.
    with pytest.raises(CollectionError) as captured:
        _ = prepare_page_commit(ctx, page, IdSequence())
    assert captured.value.code is CollectionErrorCode.INVALID_TERMINAL_REASON


def test_accepted_post_uses_server_recomputed_content_identity() -> None:
    # Given: one full author-free item below the byte limit.
    title = "Prediction market"
    body = "Community reaction"
    item = AcceptedPostInput(
        source_post_id="post-1",
        canonical_url=AnyHttpUrl("https://example.com/post-1"),
        title=title,
        body=body,
        published_at=NOW,
        language="en",
        comments_count=None,
        upvote_or_score=None,
        content_hash=compute_content_hash(title, body),
    )
    ctx = context()

    # When: the page is prepared.
    plan = prepare_page_commit(ctx, request(ctx, posts=(item,)), IdSequence())

    # Then: one version mutation and one ordered accepted result are bound to it.
    assert plan.outcome.response.accepted_count == 1
    assert len(plan.items) == 1
    assert plan.items[0].post_version_id is not None
    assert plan.items[0].normalized_content_hash == item.content_hash


def test_page_rejects_duplicate_source_identity_before_persistence() -> None:
    # Given: one provider page repeats the same source identity twice.
    title = "Prediction market"
    body = "Community reaction"
    item = AcceptedPostInput(
        source_post_id="post-1",
        canonical_url=AnyHttpUrl("https://example.com/post-1"),
        title=title,
        body=body,
        published_at=NOW,
        language="en",
        comments_count=None,
        upvote_or_score=None,
        content_hash=compute_content_hash(title, body),
    )
    ctx = context()

    # When/Then: the page is rejected instead of planning two conflicting posts.
    with pytest.raises(CollectionError) as captured:
        _ = prepare_page_commit(ctx, request(ctx, posts=(item, item)), IdSequence())
    assert captured.value.code is CollectionErrorCode.INVALID_CONTRACT


@pytest.mark.parametrize("violation", ["changed", "expired"])
def test_page_rejects_authorization_changed_or_expired_after_claim(
    violation: str,
) -> None:
    # Given: the locked authorization changed identity or expired at database time.
    ctx = context()
    changed = replace(
        ctx.authorization,
        decision_id=(
            UUID(int=99) if violation == "changed" else ctx.authorization.decision_id
        ),
        expires_at=NOW if violation == "expired" else ctx.authorization.expires_at,
    )

    # When/Then: the page fails closed before any commit plan is produced.
    with pytest.raises(CollectionError) as captured:
        _ = prepare_page_commit(
            replace(ctx, authorization=changed), request(ctx), IdSequence()
        )
    assert captured.value.code is CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE
