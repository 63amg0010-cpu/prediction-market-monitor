from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from app.collection import reconciliation_store
from app.collection.base import CollectionError, CollectionErrorCode
from app.collection.completion_models import CompletionContext, RunCompletionFacts
from app.collection.page_commit import prepare_page_commit
from app.domain.enums import CommandStatus, RunStatus

from .phase2_domain_helpers import facts_from_plan
from .phase2_fixtures import (
    COMMAND_ID,
    NOW,
    PageContextOverrides,
    page_context,
    page_request,
)

SOURCE_TWO = UUID("6f207e62-01f2-4da2-8d64-1545590735d8")
RUN_TWO = UUID("af824b3d-a00f-4dc5-b916-a379df04344f")


def _ready_and_unfinished() -> tuple[CompletionContext, RunCompletionFacts]:
    source_ids = (page_context().run.source_id, SOURCE_TWO)
    ready_context = page_context(PageContextOverrides(source_ids=source_ids))
    terminal = prepare_page_commit(
        ready_context,
        page_request(ready_context),
        lambda: UUID(int=401),
    )
    unfinished_context = page_context(
        PageContextOverrides(
            source_id=SOURCE_TWO,
            command_id=COMMAND_ID,
            run_id=RUN_TWO,
            source_ids=source_ids,
        )
    )
    unfinished = RunCompletionFacts(
        unfinished_context.run,
        unfinished_context.checkpoint,
        (),
        (),
        None,
    )
    return (
        CompletionContext(
            ready_context.command,
            (facts_from_plan(terminal), unfinished),
            NOW + timedelta(minutes=7),
        ),
        unfinished,
    )


def test_all_terminal_ready_stale_runs_finalize_idempotently() -> None:
    # Given: completion crashed after the only run persisted its terminal marker.
    context = page_context()
    terminal = prepare_page_commit(
        context,
        page_request(context),
        lambda: UUID(int=410),
    )
    recovery = CompletionContext(
        context.command,
        (facts_from_plan(terminal),),
        NOW + timedelta(minutes=7),
    )

    # When: reconciliation derives finalization twice from the same locked facts.
    first = reconciliation_store.prepare_stale_recovery(recovery, b"k" * 32)
    second = reconciliation_store.prepare_stale_recovery(recovery, b"k" * 32)

    # Then: both plans preserve the persisted marker and publish success once applied.
    assert first == second
    assert first is not None
    assert first.command_status is CommandStatus.SUCCEEDED
    assert first.runs[0].status is RunStatus.SUCCEEDED
    assert first.runs[0].terminal_page_commit_id == terminal.commit.id
    assert first.publications[0].terminal_page_commit_id == terminal.commit.id


def test_mixed_stale_recovery_preserves_ready_run_and_retries_only_unfinished() -> None:
    # Given: one terminal-ready run and one unfinished run share a stale command.
    recovery, unfinished = _ready_and_unfinished()
    ready = recovery.runs[0]

    # When: the server derives recovery from each run's persisted facts.
    plan = reconciliation_store.prepare_stale_recovery(recovery, b"k" * 32)

    # Then: only the unfinished source is retryable and the marker-backed run succeeds.
    assert plan is not None
    assert plan.command_status is CommandStatus.FAILED_RETRYABLE
    assert plan.runs[0].status is RunStatus.SUCCEEDED
    assert plan.runs[0].terminal_page_commit_id == ready.run.terminal_page_commit_id
    assert plan.runs[1].id == unfinished.run.id
    assert plan.runs[1].status is RunStatus.FAILED_RETRYABLE
    assert plan.runs[1].terminal_page_commit_id is None
    assert len(plan.publications) == 1


def test_recovery_never_invents_a_missing_terminal_marker() -> None:
    # Given: corrupt in-memory facts claim readiness without a terminal PageCommit.
    context = page_context()
    corrupt = replace(context.run, completion_ready_at=NOW)
    recovery = CompletionContext(
        context.command,
        (RunCompletionFacts(corrupt, context.checkpoint, (), (), None),),
        NOW + timedelta(minutes=7),
    )

    # When/Then: shared completion validation fails closed instead of synthesizing it.
    with pytest.raises(CollectionError) as captured:
        _ = reconciliation_store.prepare_stale_recovery(recovery, b"k" * 32)
    assert captured.value.code is CollectionErrorCode.TERMINAL_PAGE_MISSING
