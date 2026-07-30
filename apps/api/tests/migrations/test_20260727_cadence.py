from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import TYPE_CHECKING, cast

from alembic import command
from alembic.config import Config
from app.db import Base, models
from scripts.release_cadence_schema import execute_schema
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Connection, TextClause

API_ROOT = Path(__file__).parents[2]


def test_0011_offline_contains_retained_exact_cadence_schema() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)
    command.upgrade(config, "20260727_0010:20260727_0011", sql=True)
    ddl = output.getvalue()
    for table in (
        "cadence_epoch_contracts",
        "cadence_workflow_slots",
        "cadence_workflow_attempts",
        "cadence_attempt_source_receipts",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in ddl
        assert f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY" in ddl
    assert "cadence_expected_sources_distinct" in ddl
    assert "uq_cadence_accepted_slot" in ddl
    assert "WHERE accepted" in ddl
    assert "DROP TABLE cadence_" not in ddl


def test_0011_downgrade_invalidates_without_dropping_cadence_evidence() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)
    command.downgrade(config, "20260727_0011:20260727_0010", sql=True)
    ddl = output.getvalue()
    assert "UPDATE cadence_epoch_contracts" in ddl
    assert "SET invalidated_at = COALESCE" in ddl
    assert "DROP TABLE cadence_" not in ddl


def test_registered_orm_enforces_slot_identity_and_accepted_cas() -> None:
    assert models is not None
    dialect = postgresql.dialect()
    expected = {
        "source_cadence_epochs",
        "cadence_epoch_contracts",
        "cadence_workflow_slots",
        "cadence_workflow_attempts",
        "cadence_attempt_source_receipts",
    }
    assert expected <= set(Base.metadata.tables)
    slot = Base.metadata.tables["cadence_workflow_slots"]
    attempt = Base.metadata.tables["cadence_workflow_attempts"]
    slot_ddl = str(CreateTable(slot).compile(dialect=dialect))
    attempt_ddl = str(CreateTable(attempt).compile(dialect=dialect))
    index_ddl = "\n".join(
        str(CreateIndex(index).compile(dialect=dialect))
        for index in attempt.indexes
    )
    assert "PRIMARY KEY (cadence_epoch_id, schedule_kind, slot_key)" in slot_ddl
    assert "fk_cadence_attempt_slot" in attempt_ddl
    assert "CREATE UNIQUE INDEX uq_cadence_accepted_slot" in index_ddl
    assert "WHERE accepted" in index_ddl


def test_cadence_schema_uses_driver_sql_online_and_literal_safe_offline() -> None:
    driver_sql: list[str] = []
    offline_sql: list[str] = []

    class Driver:
        def exec_driver_sql(self, statement: str) -> None:
            driver_sql.append(statement)

    def capture(statement: TextClause) -> None:
        compiled = statement.compile(dialect=postgresql.dialect())
        assert compiled.params == {}
        offline_sql.append(str(compiled))

    execute_schema(
        cast("Connection", cast("object", Driver())),
        offline=False,
        alembic_execute=cast("Callable[[TextClause], None]", capture),
    )
    execute_schema(
        cast("Connection", cast("object", Driver())),
        offline=True,
        alembic_execute=cast("Callable[[TextClause], None]", capture),
    )
    assert any(":17:00Z" in statement for statement in driver_sql)
    assert any(":17:00Z" in statement for statement in offline_sql)
    assert all("\\:" not in statement for statement in offline_sql)
