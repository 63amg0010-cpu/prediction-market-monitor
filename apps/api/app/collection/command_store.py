"""Durable slot materialization, dispatch, and claim persistence."""

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.run_models import CollectionRun, SourceCheckpoint
from app.db.scheduler_models import CollectionCommand

from .base import CollectionError, CollectionErrorCode
from .checkpoint import CheckpointState, RunStart, RunState, start_run
from .claim_policy_store import ClaimSourcePolicy
from .commands import CommandState, collection_source_set_hash
from .dispatch import (
    ClaimCredentials,
    DispatchConfirmation,
    DispatchReservation,
    claim_command,
    confirm_dispatch,
    reserve_dispatch,
)
from .orm_state import apply_command_state, to_command_state, to_run_state
from .retry_source_store import eligible_retry_sources


@dataclass(frozen=True, slots=True)
class ReserveOperation:
    """Command identity paired with generated reservation secrets."""

    command_id: UUID
    reservation: DispatchReservation


@dataclass(frozen=True, slots=True)
class ConfirmOperation:
    """Command identity paired with GitHub acceptance facts."""

    command_id: UUID
    confirmation: DispatchConfirmation


@dataclass(frozen=True, slots=True)
class ClaimOperation:
    """Claim proofs and the source set committed by its hash."""

    command_id: UUID
    credentials: ClaimCredentials
    source_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Claimed command and server-created source runs."""

    command: CommandState
    runs: tuple[RunState, ...]


@dataclass(frozen=True, slots=True)
class CheckpointSeed:
    """Source-scope identity and database creation clock."""

    source_id: UUID
    scope_version: str
    created_at: datetime


async def reserve_command(
    session: AsyncSession,
    operation: ReserveOperation,
) -> CommandState:
    """Persist one due reservation using database time."""
    row = await _locked_command(session, operation.command_id)
    state = reserve_dispatch(
        to_command_state(row, ()),
        operation.reservation,
        await _db_now(session),
    )
    apply_command_state(row, state)
    return state


async def confirm_command(
    session: AsyncSession,
    operation: ConfirmOperation,
) -> CommandState:
    """Persist GitHub dispatch acceptance for its exact nonce."""
    row = await _locked_command(session, operation.command_id)
    state = confirm_dispatch(
        to_command_state(row, ()),
        operation.confirmation,
        await _db_now(session),
    )
    apply_command_state(row, state)
    return state


async def claim_runs(
    session: AsyncSession,
    operation: ClaimOperation,
    policies: dict[UUID, ClaimSourcePolicy],
) -> ClaimResult:
    """Claim one command and create only its eligible source-attempt runs."""
    row = await _locked_command(session, operation.command_id)
    if row.source_set_hash != collection_source_set_hash(operation.source_ids):
        raise CollectionError(CollectionErrorCode.RUN_SET_MISMATCH, 409)
    now = await _db_now(session)
    command = claim_command(
        to_command_state(row, operation.source_ids),
        operation.credentials,
        now,
    )
    current_rows = tuple(
        (
            await session.execute(
                select(CollectionRun)
                .where(
                    CollectionRun.command_id == row.id,
                    CollectionRun.attempt == row.attempt,
                )
                .order_by(CollectionRun.source_id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if current_rows:
        apply_command_state(row, command)
        return ClaimResult(command, tuple(to_run_state(run) for run in current_rows))
    if row.lease_hash is None:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    eligible = await eligible_retry_sources(
        session,
        operation.command_id,
        operation.source_ids,
    )
    runs: list[RunState] = []
    for source_id in eligible:
        checkpoint_row = await _checkpoint(
            session,
            CheckpointSeed(source_id, row.scope_version, now),
        )
        policy = policies.get(source_id)
        if policy is None:
            raise CollectionError(
                CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403
            )
        run = replace(
            start_run(
                RunStart(
                    uuid4(),
                    row.id,
                    source_id,
                    row.scope_version,
                    row.attempt,
                    row.lease_hash,
                    now,
                ),
                CheckpointState(
                    checkpoint_row.id,
                    checkpoint_row.source_id,
                    checkpoint_row.scope_version,
                    checkpoint_row.revision,
                    checkpoint_row.cursor,
                    checkpoint_row.watermark_published_at,
                    checkpoint_row.watermark_source_post_id,
                    checkpoint_row.last_completed_run_id,
                ),
            ),
            authorization_decision_id=policy.authorization.decision_id,
            authorization_snapshot=policy.authorization.model_dump(mode="json"),
            budget_decision_id=policy.budget_decision_id,
            budget_decision_status=policy.budget_status,
            reviewed_page_cap=policy.reviewed_page_cap,
            reviewed_post_cap=policy.reviewed_post_cap,
            skip_budget_decision_id=policy.skip_budget_decision_id,
        )
        session.add(_run_row(run, now))
        runs.append(run)
    if not runs:
        raise CollectionError(CollectionErrorCode.RUN_SET_MISMATCH, 409)
    apply_command_state(row, command)
    return ClaimResult(command, tuple(runs))


async def _checkpoint(
    session: AsyncSession,
    seed: CheckpointSeed,
) -> SourceCheckpoint:
    row = (
        await session.execute(
            select(SourceCheckpoint)
            .where(
                SourceCheckpoint.source_id == seed.source_id,
                SourceCheckpoint.scope_version == seed.scope_version,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = SourceCheckpoint(
        id=uuid4(),
        source_id=seed.source_id,
        scope_version=seed.scope_version,
        cursor=None,
        revision=0,
        updated_at=seed.created_at,
    )
    session.add(row)
    return row


def _run_row(run: RunState, now: datetime) -> CollectionRun:
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
        next_page_ordinal=0,
        committed_page_count=0,
        final_cursor=run.final_cursor,
        lease_identity_hash=run.lease_identity_hash,
        authorization_decision_id=run.authorization_decision_id,
        authorization_snapshot=run.authorization_snapshot,
        budget_decision_id=run.budget_decision_id,
        budget_decision_status=run.budget_decision_status,
        reviewed_page_cap=run.reviewed_page_cap,
        reviewed_post_cap=run.reviewed_post_cap,
        skip_budget_decision_id=run.skip_budget_decision_id,
        started_at=run.started_at,
        heartbeat_at=run.heartbeat_at,
        created_at=now,
    )


async def _locked_command(
    session: AsyncSession,
    command_id: UUID,
) -> CollectionCommand:
    row = (
        await session.execute(
            select(CollectionCommand)
            .where(CollectionCommand.id == command_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    return row


async def _db_now(session: AsyncSession) -> datetime:
    clock = func.clock_timestamp(type_=DateTime(timezone=True))
    return (await session.execute(select(clock))).scalar_one()
