from __future__ import annotations

# ruff: noqa: ANN401
# pyright: reportAny=false, reportArgumentType=false, reportExplicitAny=false
# pyright: reportUnannotatedClassAttribute=false, reportUnusedCallResult=false
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from scripts.release_privacy_contracts import IncidentScope
from scripts.release_rollback_models import RollbackMutationIntent
from scripts.release_runtime_database import (
    DatabaseRuntimeError,
    engine_from_named_env,
    read_only_repeatable_read,
)
from scripts.release_runtime_mutations import (
    AuthorizedPrivacyTransactions,
    MutationRuntimeError,
    TransactionalRollbackAdapter,
)

NOW = datetime(2026, 7, 29, tzinfo=UTC)
NONCE = UUID("11111111-1111-4111-8111-111111111111")


class Result:
    def __init__(
        self,
        *,
        row: tuple[object, ...] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._row = row
        self.rowcount = rowcount

    def one_or_none(self) -> tuple[object, ...] | None:
        return self._row


class Connection:
    def __init__(self, rows: list[tuple[object, ...] | None]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    async def execute(
        self,
        statement: object,
        _params: object = None,
    ) -> Result:
        sql = str(statement)
        self.calls.append(sql)
        if "ORDER BY transition_at_db" in sql:
            return Result(row=self.rows.pop(0))
        return Result()

    async def scalar(self, statement: object) -> object:
        self.calls.append(str(statement))
        return NOW

    def begin(self) -> Context:
        return Context(self)


class Context:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> Any:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        return None


class Engine:
    def __init__(self, connection: Connection) -> None:
        self.connection = connection
        self.begin_calls = 0

    def connect(self) -> Context:
        return Context(self.connection)

    def begin(self) -> Context:
        self.begin_calls += 1
        return Context(self.connection)


def test_named_env_fails_before_engine_creation() -> None:
    calls: list[str] = []

    def factory(value: str) -> Any:
        calls.append(value)
        return object()

    with pytest.raises(DatabaseRuntimeError, match="database_url_environment_empty"):
        _ = engine_from_named_env("DB_URL", environ={}, factory=factory)
    assert calls == []
    assert engine_from_named_env(
        "DB_URL",
        environ={"DB_URL": "postgresql+asyncpg://redacted"},
        factory=factory,
    )
    assert calls == ["postgresql+asyncpg://redacted"]


@pytest.mark.asyncio
async def test_production_snapshot_is_read_only_repeatable_read_first() -> None:
    connection = Connection([])
    engine = Engine(connection)

    async def reader(current: Connection, observed_at: datetime) -> str:
        assert current is connection
        assert observed_at == NOW
        current.calls.append("reader")
        return "ok"

    assert await read_only_repeatable_read(engine, reader) == "ok"  # type: ignore[arg-type]
    assert "REPEATABLE READ READ ONLY" in connection.calls[0]
    assert "transaction_timestamp" in connection.calls[1]
    assert connection.calls[2] == "reader"


def _intent(incident: str = "technical") -> RollbackMutationIntent:
    return RollbackMutationIntent(
        advisory_lock_namespace="source-binding",
        activation_nonce=NONCE,
        expected_latest_transition="restore_writing",
        expected_latest_transition_id=7,
        next_transition="restored",
        incident_class=incident,  # type: ignore[arg-type]
        predecessor_receipt_sha256="a" * 64,
        matrix_b_chain_sha256="b" * 64,
        receipt_body={"receipt_sha256": "c" * 64},
    )


@pytest.mark.asyncio
async def test_rollback_lock_then_cas_and_rejects_privacy_shortcut() -> None:
    connection = Connection([(7, "restore_writing")])
    engine = Engine(connection)
    await TransactionalRollbackAdapter(engine).finalize(_intent())  # type: ignore[arg-type]
    assert "pg_advisory_xact_lock" in connection.calls[0]
    assert "FOR UPDATE" in connection.calls[1]
    assert "INSERT INTO source_activation_state_transitions" in connection.calls[2]

    blocked = Engine(Connection([]))
    with pytest.raises(MutationRuntimeError, match="rollback_intent_unauthorized"):
        await TransactionalRollbackAdapter(blocked).finalize(_intent("privacy"))  # type: ignore[arg-type]
    assert blocked.begin_calls == 0


def _scope() -> IncidentScope:
    return IncidentScope(
        source_id=NONCE,
        epoch_id=NONCE,
        activation_nonce=NONCE,
        violation_kind="privacy",
        predecessor_sha256="a" * 64,
        reviewed_sha="b" * 40,
        approved_plan_sha256="c" * 64,
    )


@pytest.mark.asyncio
async def test_privacy_terminal_transition_only_from_restore_writing() -> None:
    active = Engine(Connection([(4, "active")]))
    called = False

    async def mutation(_connection: object) -> str:
        nonlocal called
        called = True
        return "done"

    with pytest.raises(MutationRuntimeError, match="privacy_restore_unauthorized"):
        await AuthorizedPrivacyTransactions(active).verify_and_restore(  # type: ignore[arg-type]
            _scope(), mutation
        )
    assert not called

    restoring = Engine(Connection([(5, "restore_writing")]))
    result = await AuthorizedPrivacyTransactions(  # type: ignore[arg-type]
        restoring
    ).verify_and_restore(_scope(), mutation)
    assert result == "done"
    assert called
