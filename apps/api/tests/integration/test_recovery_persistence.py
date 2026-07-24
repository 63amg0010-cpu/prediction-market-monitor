from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from app.collection import reconciliation_store
from app.collection.commands import collection_source_set_hash
from app.collection.completion_context_store import LockedCompletionContext
from app.collection.completion_models import (
    CompletionContext,
    CompletionPlan,
    CompletionPublicationResponse,
    RunCompletionFacts,
)
from app.collection.completion_plan_store import persist_completion_plan
from app.collection.page_commit import prepare_page_commit
from app.db.publication_models import SourcePublicationSequence
from app.db.run_models import CollectionRun, SourceCheckpoint
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import (
    CommandStatus,
    RunStatus,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .phase2_domain_helpers import facts_from_plan
from .phase2_fixtures import NOW, PageContextOverrides, page_context, page_request

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.sql.base import Executable

SOURCE_TWO = UUID("6f207e62-01f2-4da2-8d64-1545590735d8")
RUN_TWO = UUID("af824b3d-a00f-4dc5-b916-a379df04344f")


@dataclass(frozen=True, slots=True)
class _ScalarValues[T]:
    values: tuple[T, ...]

    def all(self) -> tuple[T, ...]:
        return self.values


@dataclass(frozen=True, slots=True)
class _ScalarResult[T]:
    values: tuple[T, ...]

    def scalars(self) -> _ScalarValues[T]:
        return _ScalarValues(self.values)

    def scalar_one_or_none(self) -> T | None:
        assert len(self.values) <= 1
        return self.values[0] if self.values else None


@dataclass(frozen=True, slots=True)
class _ClockResult:
    value: datetime

    def scalar_one(self) -> datetime:
        return self.value


type _ExecuteResult = (
    _ClockResult
    | _ScalarResult[CollectionCommand]
    | _ScalarResult[CollectionRun]
    | _ScalarResult[SourcePublicationSequence]
    | _ScalarResult[UUID]
)


def _completion_context(*, mixed: bool) -> CompletionContext:
    source_ids = (
        (page_context().run.source_id, SOURCE_TWO)
        if mixed
        else (page_context().run.source_id,)
    )
    ready_context = page_context(PageContextOverrides(source_ids=source_ids))
    terminal = prepare_page_commit(
        ready_context,
        page_request(ready_context),
        lambda: UUID(int=501),
    )
    facts: tuple[RunCompletionFacts, ...] = (facts_from_plan(terminal),)
    if mixed:
        unfinished_context = page_context(
            PageContextOverrides(
                source_id=SOURCE_TWO,
                command_id=ready_context.command.id,
                run_id=RUN_TWO,
                source_ids=source_ids,
            )
        )
        facts += (
            RunCompletionFacts(
                unfinished_context.run,
                unfinished_context.checkpoint,
                (),
                (),
                None,
            ),
        )
    return CompletionContext(
        ready_context.command,
        facts,
        NOW + timedelta(minutes=7),
    )


def _command_row(context: CompletionContext) -> CollectionCommand:
    command = context.command
    return CollectionCommand(
        id=command.id,
        slot_id=None,
        scope_version=command.scope_version,
        source_set_hash=collection_source_set_hash(command.source_ids),
        kind=command.kind,
        idempotency_key="tests/recovery",
        status=command.status,
        attempt=command.attempt,
        available_at=command.available_at,
        claimed_at=command.claimed_at,
        heartbeat_at=command.heartbeat_at,
        lease_hash=command.lease_hash,
        created_at=NOW,
    )


def _run_row(facts: RunCompletionFacts) -> CollectionRun:
    run = facts.run
    return CollectionRun(
        id=run.id,
        command_id=run.command_id,
        source_id=run.source_id,
        scope_version=run.scope_version,
        attempt=run.attempt,
        status=run.status,
        start_checkpoint_revision=run.start_checkpoint_revision,
        start_cursor=run.start_cursor,
        genesis_chain_hash=run.genesis_chain_hash,
        committed_page_hash_chain=run.committed_page_hash_chain,
        next_page_ordinal=run.next_page_ordinal,
        committed_page_count=run.committed_page_count,
        final_cursor=run.final_cursor,
        completion_ready_at=run.completion_ready_at,
        lease_identity_hash=run.lease_identity_hash,
        created_at=NOW,
    )


async def _run_recovery(
    monkeypatch: pytest.MonkeyPatch,
    context: CompletionContext,
) -> tuple[
    CommandStatus,
    tuple[RunStatus, ...],
    tuple[UUID, ...],
    tuple[UUID | None, ...],
]:
    command_row = _command_row(context)
    run_rows = tuple(_run_row(facts) for facts in context.runs)
    checkpoint_rows = tuple(
        SourceCheckpoint(
            id=facts.checkpoint.id,
            source_id=facts.checkpoint.source_id,
            scope_version=facts.checkpoint.scope_version,
            cursor=facts.checkpoint.cursor,
            revision=facts.checkpoint.revision,
            updated_at=context.db_now,
        )
        for facts in context.runs
    )
    locked = LockedCompletionContext(context, command_row, run_rows, checkpoint_rows)
    persisted: list[CompletionPublicationResponse] = []
    responses: list[_ExecuteResult] = [
        _ClockResult(context.db_now),
        _ScalarResult((command_row,)),
        _ScalarResult(run_rows),
        _ScalarResult[SourcePublicationSequence](()),
        _ScalarResult[UUID](()),
    ]

    async def execute(_statement: Executable) -> _ExecuteResult:
        return responses.pop(0)

    async def load(
        _session: AsyncSession, _command_id: UUID, _attempt: int
    ) -> LockedCompletionContext:
        return locked

    async def persist(
        session: AsyncSession,
        current: LockedCompletionContext,
        plan: CompletionPlan,
        retry_jitter_key: bytes,
    ) -> tuple[CompletionPublicationResponse, ...]:
        responses = await persist_completion_plan(
            session, current, plan, retry_jitter_key
        )
        persisted.extend(responses)
        return responses

    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        monkeypatch.setattr(
            reconciliation_store,
            "load_locked_completion_context",
            load,
            raising=False,
        )
        monkeypatch.setattr(reconciliation_store, "persist_completion_plan", persist)
        due = await reconciliation_store.recover_due_commands(
            session, "scope-v1", b"k" * 32
        )
    assert due == ()
    assert responses == []
    return (
        command_row.status,
        tuple(row.status for row in run_rows),
        tuple(item.run_id for item in persisted),
        tuple(row.terminal_page_commit_id for row in run_rows),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mixed", [False, True])
async def test_sql_recovery_persists_shared_finalizer_plan(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mixed: bool,
) -> None:
    # Given: locked ORM rows for a stale command with a terminal-ready subset.
    context = _completion_context(mixed=mixed)

    # When: due-command reconciliation crosses the production SQL store boundary.
    (
        command_status,
        run_statuses,
        publication_run_ids,
        terminal_ids,
    ) = await _run_recovery(monkeypatch, context)

    # Then: the shared finalizer plan, including its publication, is persisted once.
    assert publication_run_ids == (context.runs[0].run.id,)
    assert run_statuses[0] is RunStatus.SUCCEEDED
    assert terminal_ids[0] == context.runs[0].run.terminal_page_commit_id
    if mixed:
        assert command_status is CommandStatus.FAILED_RETRYABLE
        assert run_statuses[1] is RunStatus.FAILED_RETRYABLE
        assert terminal_ids[1] is None
    else:
        assert command_status is CommandStatus.SUCCEEDED
