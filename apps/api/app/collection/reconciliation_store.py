"""Database-time recovery of abandoned and due collection commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import DateTime, func, select

from app.db.run_models import CollectionRun
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import CommandStatus

from .completion_context_store import load_locked_completion_context
from .completion_plan_store import persist_completion_plan
from .dispatch import StaleCheck, mark_stale
from .orm_state import apply_command_state, to_command_state
from .recovery import prepare_stale_recovery

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

_RECONCILABLE = (
    CommandStatus.DISPATCH_RESERVED,
    CommandStatus.DISPATCHED,
    CommandStatus.RUNNING,
    CommandStatus.STALE_ABANDONED,
)


async def recover_due_commands(
    session: AsyncSession,
    scope_version: str,
    retry_jitter_key: bytes,
) -> tuple[UUID, ...]:
    """Reconcile stale commands, then return every database-time due command."""
    now = await _database_now(session)
    rows = tuple(
        (
            await session.execute(
                select(CollectionCommand)
                .where(
                    CollectionCommand.scope_version == scope_version,
                    CollectionCommand.status.in_(_RECONCILABLE),
                )
                .order_by(CollectionCommand.created_at, CollectionCommand.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await _reconcile_command(session, row, now, retry_jitter_key)
    return tuple(
        (
            await session.execute(
                select(CollectionCommand.id)
                .where(
                    CollectionCommand.scope_version == scope_version,
                    CollectionCommand.status.in_(
                        (CommandStatus.QUEUED, CommandStatus.FAILED_RETRYABLE)
                    ),
                    CollectionCommand.available_at <= now,
                )
                .order_by(CollectionCommand.available_at, CollectionCommand.id)
                .with_for_update(of=CollectionCommand)
            )
        )
        .scalars()
        .all()
    )


async def _reconcile_command(
    session: AsyncSession,
    row: CollectionCommand,
    now: datetime,
    retry_jitter_key: bytes,
) -> None:
    run_rows = tuple(
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
    recoverable_run = row.status in (
        CommandStatus.RUNNING,
        CommandStatus.STALE_ABANDONED,
    )
    if recoverable_run and run_rows:
        locked = await load_locked_completion_context(session, row.id, row.attempt)
        plan = prepare_stale_recovery(locked.domain, retry_jitter_key)
        if plan is not None:
            _ = await persist_completion_plan(
                session,
                locked,
                plan,
                retry_jitter_key,
            )
        return
    check = StaleCheck(
        db_now=now,
        completion_ready=False,
        retry_jitter_key=retry_jitter_key,
    )
    original_status = row.status
    state = mark_stale(
        to_command_state(row, tuple(run.source_id for run in run_rows)), check
    )
    if state.status is CommandStatus.STALE_ABANDONED:
        state = mark_stale(state, check)
    apply_command_state(row, state)
    if original_status is state.status:
        return


async def _database_now(session: AsyncSession) -> datetime:
    clock = func.clock_timestamp(type_=DateTime(timezone=True))
    return (await session.execute(select(clock))).scalar_one()


__all__ = ("prepare_stale_recovery", "recover_due_commands")
