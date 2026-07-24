"""Locked PostgreSQL snapshots for atomic command finalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, func, select

from app.db.auth_models import CommunitySource, SourceAuthorizationDecision
from app.db.operations_models import BudgetDecision
from app.db.page_models import PageCommit, PageCommitItem
from app.db.run_models import CollectionRun, SourceCheckpoint
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import AuthorizationStatus, BudgetDecisionStatus, RunStatus

from .authorization import AuthorizationSnapshot, require_active_authorization
from .base import CollectionError, CollectionErrorCode
from .completion_models import (
    CompletionContext,
    ObservedPostVersion,
    RunCompletionFacts,
    SkipDecisionProof,
)
from .orm_state import (
    to_checkpoint_state,
    to_command_state,
    to_page_commit_record,
    to_run_state,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class LockedCompletionContext:
    """Domain finalization facts paired with their mutable rows."""

    domain: CompletionContext
    command_row: CollectionCommand
    run_rows: tuple[CollectionRun, ...]
    checkpoint_rows: tuple[SourceCheckpoint, ...]


async def load_locked_completion_context(
    session: AsyncSession,
    command_id: UUID,
    attempt: int,
) -> LockedCompletionContext:
    """Lock a command, its exact attempt runs, and every source checkpoint."""
    command_row = (
        await session.execute(
            select(CollectionCommand)
            .where(CollectionCommand.id == command_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if command_row is None:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    clock = func.clock_timestamp(type_=DateTime(timezone=True))
    db_now: datetime = (await session.execute(select(clock))).scalar_one()
    run_rows = tuple(
        (
            await session.execute(
                select(CollectionRun)
                .where(
                    CollectionRun.command_id == command_id,
                    CollectionRun.attempt == attempt,
                )
                .order_by(CollectionRun.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    checkpoints: list[SourceCheckpoint] = []
    facts: list[RunCompletionFacts] = []
    for run_row in run_rows:
        await _require_completion_authorization(session, run_row, db_now)
        checkpoint = (
            await session.execute(
                select(SourceCheckpoint)
                .where(
                    SourceCheckpoint.source_id == run_row.source_id,
                    SourceCheckpoint.scope_version == run_row.scope_version,
                )
                .with_for_update()
            )
        ).scalar_one()
        checkpoints.append(checkpoint)
        commits = tuple(
            to_page_commit_record(row)
            for row in (
                (
                    await session.execute(
                        select(PageCommit)
                        .where(PageCommit.run_id == run_row.id)
                        .order_by(PageCommit.page_ordinal)
                    )
                )
                .scalars()
                .all()
            )
        )
        item_rows = tuple(
            (
                await session.execute(
                    select(PageCommitItem)
                    .join(PageCommit, PageCommitItem.page_commit_id == PageCommit.id)
                    .where(
                        PageCommit.run_id == run_row.id,
                        PageCommitItem.post_version_id.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        observed = tuple(
            ObservedPostVersion(item.post_version_id, item.normalized_content_hash)
            for item in item_rows
            if item.post_version_id is not None
        )
        facts.append(
            RunCompletionFacts(
                to_run_state(run_row),
                to_checkpoint_state(checkpoint),
                commits,
                observed,
                await _skip_proof(session, run_row),
            )
        )
    source_ids = tuple(run.source_id for run in run_rows)
    return LockedCompletionContext(
        CompletionContext(
            to_command_state(command_row, source_ids), tuple(facts), db_now
        ),
        command_row,
        run_rows,
        tuple(checkpoints),
    )


async def _skip_proof(
    session: AsyncSession,
    run: CollectionRun,
) -> SkipDecisionProof | None:
    authorization_id = run.skip_authorization_decision_id
    budget_id = run.skip_budget_decision_id
    if authorization_id is not None and budget_id is not None:
        raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)
    if authorization_id is not None:
        decision = await session.get(SourceAuthorizationDecision, authorization_id)
        if (
            decision is None
            or decision.source_id != run.source_id
            or decision.status
            not in (
                AuthorizationStatus.DENIED,
                AuthorizationStatus.REVOKED,
                AuthorizationStatus.EXPIRED,
            )
        ):
            raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)
        return SkipDecisionProof(authorization_id, RunStatus.SKIPPED_POLICY)
    if budget_id is None:
        return None
    budget = await session.get(BudgetDecision, budget_id)
    if (
        budget is None
        or budget.status is not BudgetDecisionStatus.HARD_STOP
        or (budget.source_id is not None and budget.source_id != run.source_id)
    ):
        raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)
    return SkipDecisionProof(budget_id, RunStatus.SKIPPED_QUOTA)


async def _require_completion_authorization(
    session: AsyncSession,
    run: CollectionRun,
    now: datetime,
) -> None:
    source = (
        await session.execute(
            select(CommunitySource)
            .where(CommunitySource.id == run.source_id)
            .with_for_update()
        )
    ).scalar_one()
    decision = (
        await session.execute(
            select(SourceAuthorizationDecision)
            .where(
                SourceAuthorizationDecision.id == source.active_authorization_id,
                SourceAuthorizationDecision.source_id == source.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run.skip_authorization_decision_id is not None:
        valid_skip = (
            decision is not None
            and decision.id == run.skip_authorization_decision_id
            and not source.enabled
            and decision.status
            in (
                AuthorizationStatus.DENIED,
                AuthorizationStatus.REVOKED,
                AuthorizationStatus.EXPIRED,
            )
        )
        if not valid_skip:
            raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)
        return
    if decision is None or decision.id != run.authorization_decision_id:
        raise CollectionError(CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403)
    _ = require_active_authorization(
        AuthorizationSnapshot(
            decision.id,
            source.id,
            source.scope_version,
            source.enabled,
            decision.status,
            decision.effective_at,
            decision.expires_at,
            decision.revoked_at,
        ),
        run.source_id,
        run.scope_version,
        now,
    )
