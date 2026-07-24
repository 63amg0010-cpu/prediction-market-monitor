from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from app.collection import command_store
from app.collection.adapters.models import HttpMethod, SourceAuthorizationDecision
from app.collection.base import hash_token
from app.collection.claim_policy_store import ClaimSourcePolicy
from app.collection.command_store import ClaimOperation
from app.collection.commands import collection_source_set_hash
from app.collection.completion_models import CompletionContext, RunCompletionFacts
from app.collection.dispatch import ClaimCredentials
from app.collection.page_commit import prepare_page_commit
from app.db.run_models import CollectionRun, SourceCheckpoint
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import (
    AuthorizationStatus,
    BudgetDecisionStatus,
    CommandStatus,
    RunStatus,
    SourcePlatform,
)
from sqlalchemy.ext.asyncio import AsyncSession

from .phase2_domain_helpers import facts_from_plan
from .phase2_fixtures import (
    LEASE,
    NOW,
    PageContextOverrides,
    page_context,
    page_request,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.sql.base import Executable

SOURCE_TWO = UUID("6f207e62-01f2-4da2-8d64-1545590735d8")
RUN_TWO = UUID("af824b3d-a00f-4dc5-b916-a379df04344f")
RESERVATION = "r" * 43


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
    | _ScalarResult[SourceCheckpoint]
)


def _mixed_context() -> CompletionContext:
    source_ids = (page_context().run.source_id, SOURCE_TWO)
    ready_context = page_context(PageContextOverrides(source_ids=source_ids))
    terminal = prepare_page_commit(
        ready_context,
        page_request(ready_context),
        lambda: UUID(int=701),
    )
    unfinished_context = page_context(
        PageContextOverrides(
            source_id=SOURCE_TWO,
            command_id=ready_context.command.id,
            run_id=RUN_TWO,
            source_ids=source_ids,
        )
    )
    return CompletionContext(
        ready_context.command,
        (
            facts_from_plan(terminal),
            RunCompletionFacts(
                unfinished_context.run,
                unfinished_context.checkpoint,
                (),
                (),
                None,
            ),
        ),
        NOW + timedelta(minutes=7),
    )


def _run_row(facts: RunCompletionFacts, status: RunStatus) -> CollectionRun:
    run = facts.run
    return CollectionRun(
        id=run.id,
        command_id=run.command_id,
        source_id=run.source_id,
        scope_version=run.scope_version,
        attempt=run.attempt,
        status=status,
        start_checkpoint_revision=run.start_checkpoint_revision,
        start_cursor=run.start_cursor,
        genesis_chain_hash=run.genesis_chain_hash,
        committed_page_hash_chain=run.committed_page_hash_chain,
        next_page_ordinal=run.next_page_ordinal,
        committed_page_count=run.committed_page_count,
        final_cursor=run.final_cursor,
        lease_identity_hash=run.lease_identity_hash,
        created_at=NOW,
    )


def _retry_command(context: CompletionContext) -> CollectionCommand:
    command = context.command
    return CollectionCommand(
        id=command.id,
        slot_id=None,
        scope_version=command.scope_version,
        source_set_hash=collection_source_set_hash(command.source_ids),
        kind=command.kind,
        idempotency_key="tests/retry-selection",
        status=CommandStatus.DISPATCH_RESERVED,
        attempt=3,
        available_at=context.db_now,
        reservation_started_at=context.db_now,
        reservation_nonce_hash=hash_token(RESERVATION),
        lease_hash=hash_token(LEASE),
        created_at=NOW,
    )


def _policy() -> ClaimSourcePolicy:
    authorization = SourceAuthorizationDecision(
        decision_id=UUID(int=702),
        source=SourcePlatform.REDDIT,
        status=AuthorizationStatus.APPROVED,
        evidence_sha256="a" * 64,
        evidence_location="https://example.test/evidence",
        issuer="provider",
        reviewer="owner",
        permitted_methods=frozenset({HttpMethod.GET}),
        permitted_routes=frozenset({"/r/test/new"}),
        permitted_fields=frozenset({"title"}),
        permitted_subreddits=frozenset({"test"}),
        purpose="tests",
        requests_per_minute=1,
        concurrency=1,
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        revoked_at=None,
    )
    return ClaimSourcePolicy(
        SOURCE_TWO,
        authorization,
        UUID(int=703),
        BudgetDecisionStatus.ALLOW,
        4,
        20,
        None,
    )


@pytest.mark.asyncio
async def test_retry_claim_creates_run_only_for_unfinished_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the latest retry succeeded one source and left the other retryable.
    context = _mixed_context()
    command = _retry_command(context)
    ready = _run_row(context.runs[0], RunStatus.SUCCEEDED)
    ready.id = UUID(int=704)
    ready.attempt = 2
    ready_old = _run_row(context.runs[0], RunStatus.FAILED_RETRYABLE)
    ready_old.id = UUID(int=705)
    unfinished = _run_row(context.runs[1], RunStatus.FAILED_RETRYABLE)
    unfinished.id = UUID(int=706)
    unfinished.attempt = 2
    unfinished_old = _run_row(context.runs[1], RunStatus.FAILED_RETRYABLE)
    unfinished_old.id = UUID(int=707)
    checkpoint = SourceCheckpoint(
        id=context.runs[1].checkpoint.id,
        source_id=SOURCE_TWO,
        scope_version="scope-v1",
        cursor=None,
        revision=0,
        updated_at=context.db_now,
    )
    responses: list[_ExecuteResult] = [
        _ScalarResult((command,)),
        _ClockResult(context.db_now),
        _ScalarResult[CollectionRun](()),
        _ScalarResult((ready, ready_old, unfinished, unfinished_old)),
        _ScalarResult((checkpoint,)),
    ]

    async def execute(_statement: Executable) -> _ExecuteResult:
        return responses.pop(0)

    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        result = await command_store.claim_runs(
            session,
            ClaimOperation(
                command.id,
                ClaimCredentials(3, LEASE, RESERVATION),
                context.command.source_ids,
            ),
            {SOURCE_TWO: _policy()},
        )

    # When/Then: the new claim contains only the source left retryable by recovery.
    assert responses == []
    assert tuple(run.source_id for run in result.runs) == (SOURCE_TWO,)
