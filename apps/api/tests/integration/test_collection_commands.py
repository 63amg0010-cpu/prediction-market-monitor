"""Phase 2 command completion, recovery, and crash acceptance gates."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Final
from uuid import UUID

import pytest
from app.collection.base import CollectionError, CollectionErrorCode, hash_token
from app.collection.checkpoint import RunStart, checkpoint_replay, start_run
from app.collection.completion import CompletionContext, prepare_completion
from app.collection.completion_models import (
    CompletionRequest,
    CompletionSourceOutcome,
    FailureClass,
    FailureDetail,
    RunCompletionFacts,
    SkipDecisionProof,
)
from app.collection.page_commit import prepare_page_commit
from app.domain.enums import RunStatus

from .phase2_domain_helpers import facts_from_plan, success_outcome
from .phase2_fixtures import (
    COMMAND_ID,
    LEASE,
    NOW,
    SOURCE_ID,
    PageContextOverrides,
    PageRequestOverrides,
    page_context,
    page_request,
)

SECOND_SOURCE_ID: Final = UUID("a2f55aec-8247-4c77-aec2-1ef76f0ea7d3")
SECOND_RUN_ID: Final = UUID("76bf9a0a-b6a0-4a5b-aef7-9a614f7c84b8")
FIRST_RUN_ID: Final = UUID("a7dd6bbd-f59a-49a4-92f2-7ae5d72352b6")


def test_complete_atomically_terminalizes_runs() -> None:
    # Given: two source runs in one command, each with a persisted terminal page.
    source_ids = (SOURCE_ID, SECOND_SOURCE_ID)
    first = page_context(
        PageContextOverrides(
            source_id=SOURCE_ID,
            run_id=FIRST_RUN_ID,
            source_ids=source_ids,
        )
    )
    second = page_context(
        PageContextOverrides(
            source_id=SECOND_SOURCE_ID,
            run_id=SECOND_RUN_ID,
            source_ids=source_ids,
        )
    )
    first_plan = prepare_page_commit(first, page_request(first), lambda: UUID(int=100))
    second_plan = prepare_page_commit(
        second, page_request(second), lambda: UUID(int=200)
    )
    first_facts = facts_from_plan(first_plan)
    second_facts = facts_from_plan(second_plan)

    # When: completion validates all run outcomes before constructing mutations.
    request = CompletionRequest(
        completion_idempotency_key=UUID(int=801),
        attempt=1,
        lease_token=LEASE,
        source_outcomes=(success_outcome(second_facts), success_outcome(first_facts)),
    )
    plan = prepare_completion(
        CompletionContext(first.command, (first_facts, second_facts), NOW), request
    )

    # Then: both runs, both publications, and the command transition are one plan.
    assert plan.command_status.value == "succeeded"
    assert tuple(run.status for run in plan.runs) == (
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
    )
    assert {publication.run_id for publication in plan.publications} == {
        FIRST_RUN_ID,
        SECOND_RUN_ID,
    }
    assert first.run.status is RunStatus.RUNNING
    assert second.run.status is RunStatus.RUNNING

    # A failed validation produces no partial plan for either run.
    invalid = request.model_copy(
        update={
            "source_outcomes": (
                success_outcome(first_facts),
                success_outcome(second_facts).model_copy(
                    update={"last_page_commit_id": UUID(int=9999)}
                ),
            )
        }
    )
    with pytest.raises(CollectionError) as captured:
        _ = prepare_completion(
            CompletionContext(first.command, (first_facts, second_facts), NOW), invalid
        )
    assert captured.value.code is CollectionErrorCode.COMPLETION_MISMATCH


def test_skip_and_partial_failure_guards() -> None:
    # Given: a source with no page commit and no server skip decision.
    context = page_context()
    empty_facts = RunCompletionFacts(context.run, context.checkpoint, (), (), None)
    unauthorized_skip = CompletionRequest(
        completion_idempotency_key=UUID(int=802),
        attempt=1,
        lease_token=LEASE,
        source_outcomes=(
            CompletionSourceOutcome(
                run_id=context.run.id,
                terminal_status=RunStatus.SKIPPED_QUOTA,
                last_page_commit_id=None,
                final_cursor=context.run.start_cursor,
                final_page_ordinal=None,
                committed_page_count=0,
                committed_page_hash_chain=context.run.genesis_chain_hash,
                skip_decision_id=None,
                failure=None,
            ),
        ),
    )

    # When: client attempts to skip without a durable policy/budget proof.
    with pytest.raises(CollectionError) as captured:
        _ = prepare_completion(
            CompletionContext(context.command, (empty_facts,), NOW), unauthorized_skip
        )

    # Then: the server rejects the skip and preserves the running state.
    assert captured.value.code is CollectionErrorCode.COMPLETION_MISMATCH
    assert context.run.status is RunStatus.RUNNING

    # A server-owned skip proof is accepted without a publication.
    decision = SkipDecisionProof(UUID(int=803), RunStatus.SKIPPED_QUOTA)
    authorized_facts = replace(empty_facts, skip_decision=decision)
    authorized_skip = unauthorized_skip.model_copy(
        update={"completion_idempotency_key": UUID(int=804)}
    )
    authorized_skip = authorized_skip.model_copy(
        update={
            "source_outcomes": (
                unauthorized_skip.source_outcomes[0].model_copy(
                    update={"skip_decision_id": decision.id}
                ),
            )
        }
    )
    skip_plan = prepare_completion(
        CompletionContext(context.command, (authorized_facts,), NOW), authorized_skip
    )
    assert skip_plan.command_status.value == "skipped"
    assert skip_plan.runs[0].status is RunStatus.SKIPPED_QUOTA
    assert skip_plan.publications == ()

    # A partial page remains durable when the source fails retryably.
    partial_context = page_context()
    partial_plan = prepare_page_commit(
        partial_context,
        page_request(
            partial_context,
            PageRequestOverrides(terminal_reason=None, next_cursor="partial-cursor"),
        ),
        lambda: UUID(int=300),
    )
    partial_facts = facts_from_plan(partial_plan)
    failure = FailureDetail.model_validate(
        {
            "class": FailureClass.RETRYABLE,
            "code": "network",
            "fingerprint": "d" * 64,
            "observed_at": NOW,
            "retry_after_at": None,
        }
    )
    failed_request = CompletionRequest(
        completion_idempotency_key=UUID(int=805),
        attempt=1,
        lease_token=LEASE,
        source_outcomes=(
            CompletionSourceOutcome(
                run_id=partial_facts.run.id,
                terminal_status=RunStatus.FAILED_RETRYABLE,
                last_page_commit_id=partial_plan.commit.id,
                final_cursor=partial_plan.commit.next_cursor,
                final_page_ordinal=partial_plan.commit.page_ordinal,
                committed_page_count=1,
                committed_page_hash_chain=partial_plan.commit.resulting_chain_hash,
                skip_decision_id=None,
                failure=failure,
            ),
        ),
    )
    failed_plan = prepare_completion(
        CompletionContext(partial_context.command, (partial_facts,), NOW),
        failed_request,
    )
    assert failed_plan.command_status.value == "failed_retryable"
    assert failed_plan.publications == ()
    assert failed_plan.runs[0].committed_page_count == 1
    assert failed_plan.runs[0].final_cursor == "partial-cursor"
    assert partial_facts.commits[0].resulting_checkpoint_revision == 1


def test_crash_before_and_after_terminal_commit() -> None:
    # Given: a nonterminal page committed before a worker crash.
    context = page_context()
    partial = prepare_page_commit(
        context,
        page_request(
            context,
            PageRequestOverrides(terminal_reason=None, next_cursor="resume-cursor"),
        ),
        lambda: UUID(int=400),
    )
    replay = checkpoint_replay(partial.updated_run, partial.updated_checkpoint)
    assert replay.expected_checkpoint_revision == 1
    assert replay.expected_cursor == "resume-cursor"
    assert replay.next_page_ordinal == 1
    assert partial.commit.page_ordinal == 0

    # When: a new attempt snapshots that checkpoint after the crash.
    resumed = start_run(
        RunStart(
            UUID(int=9001),
            COMMAND_ID,
            SOURCE_ID,
            "scope-v1",
            2,
            hash_token(LEASE),
            NOW + timedelta(minutes=1),
        ),
        partial.updated_checkpoint,
    )

    # Then: the new chain starts at ordinal zero and never crosses the old chain.
    assert resumed.start_checkpoint_revision == 1
    assert resumed.start_cursor == "resume-cursor"
    assert resumed.next_page_ordinal == 0
    assert resumed.genesis_chain_hash != partial.updated_run.genesis_chain_hash

    # A terminal commit that lost its response remains completion-ready and finalizes.
    terminal = prepare_page_commit(
        context, page_request(context), lambda: UUID(int=500)
    )
    terminal_facts = facts_from_plan(terminal)
    final = prepare_completion(
        CompletionContext(context.command, (terminal_facts,), NOW),
        CompletionRequest(
            completion_idempotency_key=UUID(int=806),
            attempt=1,
            lease_token=LEASE,
            source_outcomes=(success_outcome(terminal_facts),),
        ),
    )
    assert final.command_status.value == "succeeded"
    assert final.runs[0].status is RunStatus.SUCCEEDED
    assert final.runs[0].terminal_page_commit_id == terminal.commit.id
    assert final.publications[0].terminal_page_commit_id == terminal.commit.id
