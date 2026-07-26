from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import UUID

import pytest
from app.collection.slot_store import MaterializationOperation, materialize_slots
from app.db.scheduler_models import CollectionCommand, CollectionSlot, SchedulerCursor

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 7, 26, 9, 20, tzinfo=UTC)
SOURCE_ID = UUID("d6dc5ea1-e3af-4bfe-88ad-e4beffd22ab6")


class _ScalarRows:
    def all(self) -> list[UUID]:
        return [SOURCE_ID]


class _Result:
    def __init__(self, position: int) -> None:
        self._position: int = position

    def scalar_one(self) -> datetime:
        assert self._position == 0
        return NOW

    def scalar_one_or_none(self) -> None:
        assert self._position == 1

    def scalars(self) -> _ScalarRows:
        assert self._position == 2
        return _ScalarRows()


class _Session:
    def __init__(self) -> None:
        self._execute_count: int = 0
        self.events: list[type[object] | str] = []

    async def execute(self, _statement: object) -> _Result:
        result = _Result(self._execute_count)
        self._execute_count += 1
        return result

    def add(self, value: object) -> None:
        self.events.append(type(value))

    async def flush(self) -> None:
        self.events.append("flush")


@pytest.mark.asyncio
async def test_materialization_flushes_slot_before_referencing_command() -> None:
    session = _Session()

    command_ids = await materialize_slots(
        cast("AsyncSession", cast("object", session)),
        MaterializationOperation(
            scope_version="phase1-reviewed-v1",
            deployment_activation_at=datetime(2026, 7, 26, 8, 0, tzinfo=UTC),
        ),
    )

    assert len(command_ids) == 1
    assert session.events == [
        SchedulerCursor,
        CollectionSlot,
        "flush",
        CollectionCommand,
    ]
