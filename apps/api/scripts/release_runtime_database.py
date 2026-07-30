"""SQLAlchemy runtime ownership for release database adapters."""

# ruff: noqa: D102, D107
# pyright: reportAny=false, reportUnannotatedClassAttribute=false

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, TypeVar, cast

from apps.api.scripts.release_cadence_db import PostgresCadenceStore
from apps.api.scripts.release_cadence_models import (
    CadenceEpoch,
    CadenceSlot,
    ScheduleKind,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

if TYPE_CHECKING:
    from uuid import UUID

T = TypeVar("T")
EngineFactory = Callable[[str], AsyncEngine]
Reader = Callable[[AsyncConnection, datetime], Awaitable[T]]
READ_ONLY = text(
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
)
DATABASE_TIME = text("SELECT transaction_timestamp()")
CADENCE_CONTRACT = text(
    """
    SELECT c.cadence_epoch_id, e.cadence_anchor_at, c.window_closes_at,
           c.epoch_sha256, c.dcinside_source_id, c.manifold_source_id,
           c.binding_sha256, c.scope_sha256, c.invalidated_at
    FROM cadence_epoch_contracts c
    JOIN source_cadence_epochs e ON e.id = c.cadence_epoch_id
    WHERE c.cadence_epoch_id = :epoch_id
    """
)
CADENCE_SLOTS = text(
    """
    SELECT cadence_epoch_id, schedule_kind, slot_key, due_at,
           accepted_attempt_id IS NOT NULL AS accepted
    FROM cadence_workflow_slots
    WHERE cadence_epoch_id = :epoch_id
    ORDER BY schedule_kind, due_at, slot_key
    """
)


class DatabaseRuntimeError(RuntimeError):
    """Stable database adapter error."""


def engine_from_named_env(
    env_name: str,
    *,
    environ: Mapping[str, str] | None = None,
    factory: EngineFactory = create_async_engine,
) -> AsyncEngine:
    """Resolve a named URL before engine construction or any database I/O."""
    if not env_name:
        msg = "database_url_environment_name_empty"
        raise DatabaseRuntimeError(msg)
    value = (os.environ if environ is None else environ).get(env_name)
    if not value:
        msg = "database_url_environment_empty"
        raise DatabaseRuntimeError(msg)
    return factory(value)


async def read_only_repeatable_read[T](
    engine: AsyncEngine,
    reader: Reader[T],
) -> T:
    """Execute a snapshot callback in one explicitly read-only transaction."""
    async with engine.connect() as connection, connection.begin():
        _ = await connection.execute(READ_ONLY)
        observed_at = await connection.scalar(DATABASE_TIME)
        if not isinstance(observed_at, datetime):
            msg = "database_time_invalid"
            raise DatabaseRuntimeError(msg)
        return await reader(connection, observed_at)


@dataclass(frozen=True, slots=True)
class CadenceDurableSnapshot:
    """Immutable durable cadence state materialized under one snapshot."""

    epoch: CadenceEpoch
    slots: tuple[CadenceSlot, ...]
    observed_at: datetime
    accepted_collection_slots: int
    accepted_verifier_slots: int

class CadencePostgresRuntime:
    """Reuse the production cadence store and add read-only status snapshots."""

    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.store = PostgresCadenceStore(engine)

    @classmethod
    def from_env(
        cls,
        env_name: str,
        *,
        environ: Mapping[str, str] | None = None,
        factory: EngineFactory = create_async_engine,
    ) -> CadencePostgresRuntime:
        return cls(
            engine_from_named_env(env_name, environ=environ, factory=factory)
        )

    async def snapshot(self, epoch_id: UUID) -> CadenceDurableSnapshot:
        async def load(
            connection: AsyncConnection,
            observed_at: datetime,
        ) -> CadenceDurableSnapshot:
            row = (
                await connection.execute(
                    CADENCE_CONTRACT, {"epoch_id": epoch_id}
                )
            ).mappings().one_or_none()
            if row is None:
                msg = "cadence_epoch_not_found"
                raise DatabaseRuntimeError(msg)
            contract = cast("Mapping[str, object]", row)
            slot_rows = (
                await connection.execute(
                    CADENCE_SLOTS, {"epoch_id": epoch_id}
                )
            ).mappings()
            epoch = CadenceEpoch(
                epoch_id=cast("UUID", contract["cadence_epoch_id"]),
                anchor_at=cast("datetime", contract["cadence_anchor_at"]),
                closes_at=cast("datetime", contract["window_closes_at"]),
                epoch_sha256=str(contract["epoch_sha256"]),
                expected_source_ids=(
                    cast("UUID", contract["dcinside_source_id"]),
                    cast("UUID", contract["manifold_source_id"]),
                ),
                binding_sha256=str(contract["binding_sha256"]),
                scope_sha256=str(contract["scope_sha256"]),
                invalidated_at=cast(
                    "datetime | None", contract["invalidated_at"]
                ),
            )
            typed_rows = tuple(
                cast("Mapping[str, object]", raw) for raw in slot_rows
            )
            slots = tuple(
                CadenceSlot(
                    epoch_id=cast("UUID", item["cadence_epoch_id"]),
                    schedule_kind=cast("ScheduleKind", item["schedule_kind"]),
                    slot_key=str(item["slot_key"]),
                    due_at=cast("datetime", item["due_at"]),
                )
                for item in typed_rows
            )
            return CadenceDurableSnapshot(
                epoch,
                slots,
                observed_at,
                sum(
                    bool(item["accepted"])
                    and item["schedule_kind"] == "collection"
                    for item in typed_rows
                ),
                sum(
                    bool(item["accepted"])
                    and item["schedule_kind"] == "verifier"
                    for item in typed_rows
                ),
            )

        return await read_only_repeatable_read(self.engine, load)

    async def dispose(self) -> None:
        await self.engine.dispose()


__all__ = (
    "CadenceDurableSnapshot",
    "CadencePostgresRuntime",
    "DatabaseRuntimeError",
    "engine_from_named_env",
    "read_only_repeatable_read",
)
