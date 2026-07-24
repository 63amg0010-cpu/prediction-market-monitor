from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.collection.authorization import AuthorizationSnapshot
from app.collection.base import (
    CollectionError,
    CollectionErrorCode,
    canonical_json_hash,
    hash_token,
)
from app.collection.checkpoint import CheckpointState, RunStart, start_run
from app.collection.commands import CommandState
from app.collection.completion import (
    CompletionContext,
    CompletionRequest,
    CompletionSourceOutcome,
    RunCompletionFacts,
    prepare_completion,
)
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

NOW = datetime(2026, 7, 20, tzinfo=UTC)
LEASE = "l" * 43


def running_context() -> tuple[CommandState, CheckpointState, PageCommitContext]:
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
    page_context = PageCommitContext(
        NOW,
        command,
        run,
        checkpoint,
        authorization,
        (),
        None,
        None,
        4,
        20,
    )
    return command, checkpoint, page_context


def terminal_page(context: PageCommitContext) -> PageCommitRequest:
    return PageCommitRequest(
        command_id=context.command.id,
        attempt=1,
        lease_token=LEASE,
        page_idempotency_key=UUID(int=10),
        expected_checkpoint_revision=0,
        expected_cursor=None,
        next_cursor=None,
        page_ordinal=0,
        posts=(),
        source_page_item_count=0,
        source_page_receipt_sha256="a" * 64,
        page_fetch_started_at=NOW,
        page_fetch_finished_at=NOW + timedelta(seconds=1),
        is_terminal_page=True,
        terminal_reason=TerminalReason.SOURCE_EXHAUSTED,
    )


def completion_request(
    run_id: UUID,
    last_commit_id: UUID,
    chain: str,
) -> CompletionRequest:
    return CompletionRequest(
        completion_idempotency_key=UUID(int=20),
        attempt=1,
        lease_token=LEASE,
        source_outcomes=(
            CompletionSourceOutcome(
                run_id=run_id,
                terminal_status=RunStatus.SUCCEEDED,
                last_page_commit_id=last_commit_id,
                final_cursor=None,
                final_page_ordinal=0,
                committed_page_count=1,
                committed_page_hash_chain=chain,
                skip_decision_id=None,
                failure=None,
            ),
        ),
    )


def test_success_requires_persisted_terminal_commit() -> None:
    # Given: a running source with no committed terminal page.
    command, checkpoint, page_context = running_context()
    request = completion_request(page_context.run.id, UUID(int=99), "0" * 64)
    facts = RunCompletionFacts(page_context.run, checkpoint, (), (), None)

    # When/Then: client-supplied terminal values cannot complete the run.
    with pytest.raises(CollectionError) as captured:
        _ = prepare_completion(CompletionContext(command, (facts,), NOW), request)
    assert captured.value.code is CollectionErrorCode.TERMINAL_PAGE_MISSING


def test_zero_data_page_completion_builds_zero_post_publication() -> None:
    # Given: an ordinal-zero empty terminal page persisted by the server.
    command, _, page_context = running_context()
    page = prepare_page_commit(
        page_context, terminal_page(page_context), lambda: UUID(int=10)
    )
    facts = RunCompletionFacts(
        page.updated_run,
        page.updated_checkpoint,
        (page.commit,),
        (),
        None,
    )
    request = completion_request(
        page.updated_run.id,
        page.commit.id,
        page.commit.resulting_chain_hash,
    )

    # When: completion is derived from the persisted marker and chain.
    plan = prepare_completion(CompletionContext(command, (facts,), NOW), request)

    # Then: publication truthfully represents an empty observed post set.
    assert plan.command_status is CommandStatus.SUCCEEDED
    assert plan.publications[0].zero_post is True
    assert plan.publications[0].post_set_hash == canonical_json_hash([])


def test_completion_recomputes_and_rejects_tampered_chain() -> None:
    # Given: a valid terminal page paired with a fabricated submitted chain.
    command, _, page_context = running_context()
    page = prepare_page_commit(
        page_context, terminal_page(page_context), lambda: UUID(int=10)
    )
    facts = RunCompletionFacts(
        page.updated_run,
        page.updated_checkpoint,
        (page.commit,),
        (),
        None,
    )
    request = completion_request(page.updated_run.id, page.commit.id, "0" * 64)

    # When/Then: the command stays pre-finalization on comparison mismatch.
    with pytest.raises(CollectionError) as captured:
        _ = prepare_completion(CompletionContext(command, (facts,), NOW), request)
    assert captured.value.code is CollectionErrorCode.COMPLETION_MISMATCH
