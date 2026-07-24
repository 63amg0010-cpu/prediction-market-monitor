"""Database-time three-hour scheduler grid materialization."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from .base import CollectionError, CollectionErrorCode, format_utc, require_utc

GRID_HOURS: Final[int] = 3
GRID_MINUTE: Final[int] = 17


@dataclass(frozen=True, slots=True)
class SchedulerCursorState:
    """Locked materialization cursor for one immutable source scope."""

    scope_version: str
    last_materialized_slot_utc: datetime | None


@dataclass(frozen=True, slots=True)
class CollectionSlotDraft:
    """One missing grid slot proposed for atomic insertion."""

    scope_version: str
    due_slot_utc: datetime
    scheduled_key: str
    materialized_at: datetime


@dataclass(frozen=True, slots=True)
class SlotMaterializationPlan:
    """Chronological inserts and the cursor value committed with them."""

    expected_cursor: SchedulerCursorState
    slots: tuple[CollectionSlotDraft, ...]
    next_cursor: SchedulerCursorState


def activation_slot(activation_at: datetime) -> datetime:
    """Return the first minute-17 three-hour grid point after activation."""
    activation = require_utc(activation_at)
    grid_hour = activation.hour - activation.hour % GRID_HOURS
    candidate = activation.replace(
        hour=grid_hour,
        minute=GRID_MINUTE,
        second=0,
        microsecond=0,
    )
    if candidate <= activation:
        candidate += timedelta(hours=GRID_HOURS)
    return candidate


def plan_slot_materialization(
    cursor: SchedulerCursorState,
    deployment_activation_at: datetime,
    db_now: datetime,
) -> SlotMaterializationPlan:
    """Plan all eligible missing slots using only database time."""
    now = require_utc(db_now)
    start = activation_slot(deployment_activation_at)
    if cursor.last_materialized_slot_utc is not None:
        last = require_utc(cursor.last_materialized_slot_utc)
        if last.minute != GRID_MINUTE or last.hour % GRID_HOURS != 0:
            raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
        start = last + timedelta(hours=GRID_HOURS)
    slots: list[CollectionSlotDraft] = []
    due = start
    while due <= now:
        slots.append(
            CollectionSlotDraft(
                scope_version=cursor.scope_version,
                due_slot_utc=due,
                scheduled_key=(f"scheduled:{cursor.scope_version}:{format_utc(due)}"),
                materialized_at=now,
            )
        )
        due += timedelta(hours=GRID_HOURS)
    next_cursor = cursor
    if slots:
        next_cursor = SchedulerCursorState(
            cursor.scope_version,
            slots[-1].due_slot_utc,
        )
    return SlotMaterializationPlan(cursor, tuple(slots), next_cursor)
