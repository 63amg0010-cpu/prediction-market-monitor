"""Durable three-hour grid-slot and command materialization."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.auth_models import CommunitySource
from app.db.scheduler_models import CollectionCommand, CollectionSlot, SchedulerCursor
from app.domain.enums import CommandKind, CommandStatus

from .base import CollectionError, CollectionErrorCode
from .commands import collection_source_set_hash
from .slots import SchedulerCursorState, plan_slot_materialization


@dataclass(frozen=True, slots=True)
class MaterializationOperation:
    """One scope activation reconciled against database time."""

    scope_version: str
    deployment_activation_at: datetime


async def materialize_slots(
    session: AsyncSession,
    operation: MaterializationOperation,
) -> tuple[UUID, ...]:
    """Persist every missed grid slot and command under one cursor lock."""
    clock = func.clock_timestamp(type_=DateTime(timezone=True))
    now: datetime = (await session.execute(select(clock))).scalar_one()
    cursor_row = (
        await session.execute(
            select(SchedulerCursor)
            .where(SchedulerCursor.scope_version == operation.scope_version)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if cursor_row is None:
        cursor_row = SchedulerCursor(
            id=uuid4(),
            scope_version=operation.scope_version,
            last_materialized_slot_utc=None,
            updated_at=now,
        )
        session.add(cursor_row)
    source_ids = tuple(
        (
            await session.execute(
                select(CommunitySource.id)
                .where(
                    CommunitySource.scope_version == operation.scope_version,
                    CommunitySource.enabled.is_(True),
                )
                .order_by(CommunitySource.id)
            )
        )
        .scalars()
        .all()
    )
    if not source_ids:
        raise CollectionError(CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403)
    plan = plan_slot_materialization(
        SchedulerCursorState(
            operation.scope_version,
            cursor_row.last_materialized_slot_utc,
        ),
        operation.deployment_activation_at,
        now,
    )
    source_hash = collection_source_set_hash(source_ids)
    command_ids: list[UUID] = []
    for draft in plan.slots:
        slot_id = uuid4()
        command_id = uuid4()
        session.add(
            CollectionSlot(
                id=slot_id,
                scope_version=draft.scope_version,
                due_slot_utc=draft.due_slot_utc,
                scheduled_key=draft.scheduled_key,
                materialized_at=draft.materialized_at,
            )
        )
        session.add(
            CollectionCommand(
                id=command_id,
                slot_id=slot_id,
                scope_version=draft.scope_version,
                source_set_hash=source_hash,
                kind=CommandKind.SCHEDULED,
                idempotency_key=draft.scheduled_key,
                status=CommandStatus.QUEUED,
                attempt=1,
                available_at=draft.due_slot_utc,
                created_at=now,
            )
        )
        command_ids.append(command_id)
    cursor_row.last_materialized_slot_utc = plan.next_cursor.last_materialized_slot_utc
    cursor_row.updated_at = now
    return tuple(command_ids)
