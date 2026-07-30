from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast, final
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from apps.api.scripts import release_cadence_sql as sql
from apps.api.scripts.release_cadence import (
    CadenceAttempt,
    CadenceError,
    SourceSubreceipt,
    materialize_epoch,
)
from apps.api.scripts.release_cadence_db import PostgresCadenceStore

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from sqlalchemy import TextClause
    from sqlalchemy.ext.asyncio import AsyncEngine

ANCHOR = datetime(2026, 8, 1, tzinfo=UTC)
SOURCES = (
    UUID("11111111-1111-4111-8111-111111111111"),
    UUID("22222222-2222-4222-8222-222222222222"),
)
BINDING = "a" * 64
SCOPE = "b" * 64


@final
class StubResult:
    def __init__(
        self,
        rows: list[dict[str, object]] | None = None,
        *,
        rowcount: int = 0,
    ) -> None:
        self.rows = rows or []
        self.rowcount = rowcount

    def mappings(self) -> StubResult:
        return self

    def one_or_none(self) -> Mapping[str, object] | None:
        return self.rows[0] if self.rows else None

    def __iter__(self) -> object:
        return iter(self.rows)


@final
class StubConnection:
    def __init__(self, *, active: bool = True) -> None:
        self.active = active
        self.events: list[TextClause] = []
        self.epoch, slots = materialize_epoch(
            uuid4(), ANCHOR, SOURCES, BINDING, SCOPE
        )
        self.slot = next(
            item for item in slots if item.schedule_kind == "collection"
        )

    async def execute(
        self,
        statement: TextClause,
        _parameters: object = None,
    ) -> StubResult:
        self.events.append(statement)
        if statement is sql.CURRENT_EPOCH:
            return StubResult(
                [
                    {
                        "cadence_anchor_at": ANCHOR,
                        "closed_at": None,
                        "current_binding_sha256": BINDING,
                        "current_cadence_id": self.epoch.epoch_id,
                        "enabled": True,
                        "recheck_at": self.epoch.closes_at,
                        "state": "active" if self.active else "failed",
                        "transition_cadence_id": self.epoch.epoch_id,
                    }
                ]
            )
        if statement is sql.SELECT_CONTRACT:
            return StubResult(
                [
                    {
                        "binding_sha256": BINDING,
                        "dcinside_source_id": SOURCES[0],
                        "epoch_sha256": self.epoch.epoch_sha256,
                        "invalidated_at": None,
                        "manifold_source_id": SOURCES[1],
                        "scope_sha256": SCOPE,
                        "window_closes_at": self.epoch.closes_at,
                    }
                ]
            )
        if statement is sql.SELECT_SLOT:
            return StubResult(
                [
                    {
                        "accepted_attempt_id": None,
                        "due_at": self.slot.due_at,
                        "schedule_kind": self.slot.schedule_kind,
                        "slot_key": self.slot.slot_key,
                    }
                ]
            )
        return StubResult(rowcount=1)


@final
class StubBegin:
    def __init__(self, connection: StubConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> StubConnection:
        return self.connection

    async def __aexit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None


@final
class StubEngine:
    def __init__(self, connection: StubConnection) -> None:
        self.connection = connection

    def begin(self) -> StubBegin:
        return StubBegin(self.connection)


def _attempt(connection: StubConnection) -> CadenceAttempt:
    epoch = connection.epoch
    slot = connection.slot
    return CadenceAttempt(
        attempt_id=uuid4(),
        epoch_id=epoch.epoch_id,
        schedule_kind=slot.schedule_kind,
        slot_key=slot.slot_key,
        mode="schedule",
        started_at=slot.due_at,
        completed_at=slot.due_at + timedelta(minutes=1),
        epoch_sha256=epoch.epoch_sha256,
        binding_sha256=BINDING,
        scope_sha256=SCOPE,
        source_subreceipts=tuple(
            SourceSubreceipt(
                source_id=source,
                succeeded=True,
                receipt_sha256="d" * 64,
            )
            for source in SOURCES
        ),
    )


def test_advisory_lock_casts_uuid_before_canonical_text_hash() -> None:
    statement = str(sql.LOCK_EPOCH)
    assert "CAST(:epoch_id AS uuid)" in statement
    assert "CAST(CAST(:epoch_id AS uuid) AS text)" in statement


@pytest.mark.asyncio
async def test_async_adapter_owns_transaction_and_cas_accepts_once() -> None:
    connection = StubConnection()
    engine = cast("AsyncEngine", cast("object", StubEngine(connection)))
    outcome = await PostgresCadenceStore(engine).record_attempt(
        connection.epoch,
        _attempt(connection),
    )
    assert outcome.accepted
    assert outcome.reason == "accepted"
    assert connection.events.count(sql.INSERT_SUBRECEIPT) == 2
    assert sql.CAS_SLOT in connection.events
    assert connection.events[-1] is sql.FINALIZE_ATTEMPT


@pytest.mark.asyncio
async def test_async_adapter_rejects_nonactive_latest_transition_before_write() -> None:
    connection = StubConnection(active=False)
    engine = cast("AsyncEngine", cast("object", StubEngine(connection)))
    with pytest.raises(CadenceError, match="latest_transition_not_active"):
        _ = await PostgresCadenceStore(engine).record_attempt(
            connection.epoch,
            _attempt(connection),
        )
    assert sql.INSERT_ATTEMPT not in connection.events
