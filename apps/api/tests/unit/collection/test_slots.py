from datetime import UTC, datetime

from app.collection.slots import (
    SchedulerCursorState,
    activation_slot,
    plan_slot_materialization,
)


def utc(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 7, 20, hour, minute, second, tzinfo=UTC)


def test_activation_slot_is_strictly_after_deployment_activation() -> None:
    # Given: activation immediately before and exactly on a grid boundary.
    before = utc(0, 16, 59)
    exact = utc(0, 17)

    # When: the first eligible slots are derived.
    before_slot = activation_slot(before)
    exact_slot = activation_slot(exact)

    # Then: the boundary is minute 17 and strictly follows activation.
    assert before_slot == utc(0, 17)
    assert exact_slot == utc(3, 17)


def test_delayed_materialization_inserts_every_missing_slot_oldest_first() -> None:
    # Given: a scheduler cursor last advanced at midnight.
    cursor = SchedulerCursorState(
        scope_version="scope-v1",
        last_materialized_slot_utc=utc(0, 17),
    )

    # When: database time has crossed three more grid boundaries.
    plan = plan_slot_materialization(cursor, utc(0, 0), utc(9, 20))

    # Then: every missed slot is returned chronologically with stable keys.
    assert tuple(slot.due_slot_utc for slot in plan.slots) == (
        utc(3, 17),
        utc(6, 17),
        utc(9, 17),
    )
    assert plan.slots[0].scheduled_key == (
        "scheduled:scope-v1:2026-07-20T03:17:00.000000Z"
    )
    assert plan.next_cursor.last_materialized_slot_utc == utc(9, 17)


def test_duplicate_invocation_materializes_no_slot() -> None:
    # Given: the cursor already covers the latest eligible database-time slot.
    cursor = SchedulerCursorState("scope-v1", utc(9, 17))

    # When: materialization is invoked again before the next boundary.
    plan = plan_slot_materialization(cursor, utc(0, 0), utc(9, 20))

    # Then: no duplicate slot is proposed and the cursor is unchanged.
    assert plan.slots == ()
    assert plan.next_cursor == cursor
