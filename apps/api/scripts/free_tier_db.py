"""Typed PostgreSQL and local HTTP measurements for the free-tier gate."""

# ruff: noqa: EM101, TRY003

from __future__ import annotations

import platform
import socket
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, Final

import httpx2
import orjson
import sqlalchemy
from apps.api.scripts.free_tier_domain import (
    FIXTURE_BYTES,
    FIXTURE_ROWS,
    GateHoldError,
    JsonObject,
    fixture_rows,
    sha256_hex,
)
from pydantic import BaseModel, ConfigDict, TypeAdapter
from sqlalchemy import DateTime, literal_column, select, text
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from pathlib import Path

INTEGER: Final[TypeAdapter[int]] = TypeAdapter(int)
_LIMITS: Final = httpx2.Limits(
    max_connections=200,
    max_keepalive_connections=40,
    keepalive_expiry=30.0,
)
_TIMEOUT: Final = httpx2.Timeout(
    connect=5.0,
    read=30.0,
    write=10.0,
    pool=10.0,
)
_SOCKET_OPTIONS: Final[list[tuple[int, int, int]]] = [
    (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
]


class ProductionAggregate(BaseModel):
    """Allowed Production aggregates, excluding every row text field."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    db_now: datetime
    transaction_read_only: str
    current_revision: str
    row_count: int
    actual_production_title_body_utf8_bytes: int
    database_bytes: int
    posts_relation_bytes: int
    post_versions_relation_bytes: int


def production_statements() -> tuple[str, ...]:
    """Return the complete unsampled Production aggregate SQL contract."""
    return (
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",
        """
SELECT
  transaction_timestamp() AS db_now,
  current_setting('transaction_read_only') AS transaction_read_only,
  (SELECT version_num FROM alembic_version) AS current_revision,
  count(*) AS row_count,
  coalesce(
    sum(octet_length(coalesce(title,'')) + octet_length(coalesce(body,''))),
    0
  ) AS actual_production_title_body_utf8_bytes,
  pg_database_size(current_database()) AS database_bytes,
  pg_total_relation_size('posts'::regclass) AS posts_relation_bytes,
  pg_total_relation_size('post_versions'::regclass) AS post_versions_relation_bytes
FROM post_versions
""".strip(),
        "COMMIT",
    )


async def production_measurement(
    database_url: str,
    expected_current: str,
) -> JsonObject:
    """Read Production aggregates in one read-only repeatable-read transaction."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="REPEATABLE READ"
            )
            async with connection.begin():
                _ = await connection.execute(text("SET TRANSACTION READ ONLY"))
                result = await connection.execute(text(production_statements()[1]))
                row = ProductionAggregate.model_validate(result.mappings().one())
        if row.transaction_read_only != "on":
            raise GateHoldError("Production transaction is writable")
        if row.current_revision != expected_current:
            raise GateHoldError("Production revision mismatch")
        return {
            "db_now": row.db_now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "row_count": row.row_count,
            "actual_production_title_body_utf8_bytes": (
                row.actual_production_title_body_utf8_bytes
            ),
            "database_bytes": row.database_bytes,
            "posts_relation_bytes": row.posts_relation_bytes,
            "post_versions_relation_bytes": row.post_versions_relation_bytes,
        }
    finally:
        await engine.dispose()


async def local_measurement(
    database_url: str,
    api_url: str,
    web_url: str,
) -> JsonObject:
    """Measure the exact local corpus and two bounded health calls."""
    rows = fixture_rows()
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            _ = await connection.execute(
                text(
                    """CREATE TEMP TABLE free_tier_fixture
                    (title text NOT NULL, body text NOT NULL)"""
                )
            )
            _ = await connection.execute(
                text(
                    "INSERT INTO free_tier_fixture(title, body) VALUES(:title, :body)"
                ),
                [{"title": title, "body": body} for title, body in rows],
            )
            before = INTEGER.validate_python(
                await connection.scalar(
                    text("SELECT pg_total_relation_size('free_tier_fixture'::regclass)")
                )
            )
            _ = await connection.execute(
                text(
                    """CREATE INDEX free_tier_fixture_search_idx
                    ON free_tier_fixture USING gin
                    (((coalesce(title,'') || ' ' || coalesce(body,''))
                    COLLATE "C") gin_trgm_ops)"""
                )
            )
            after = INTEGER.validate_python(
                await connection.scalar(
                    text("SELECT pg_total_relation_size('free_tier_fixture'::regclass)")
                )
            )
            byte_count = INTEGER.validate_python(
                await connection.scalar(
                    text(
                        """SELECT sum(octet_length(title)+octet_length(body))
                        FROM free_tier_fixture"""
                    )
                )
            )
        if byte_count != FIXTURE_BYTES:
            raise GateHoldError("local fixture is not exactly 60 MiB")
    finally:
        await engine.dispose()
    transport = httpx2.AsyncHTTPTransport(
        http2=True, retries=3, limits=_LIMITS, socket_options=_SOCKET_OPTIONS
    )
    async with httpx2.AsyncClient(
        transport=transport, timeout=_TIMEOUT, follow_redirects=True
    ) as client:
        responses = [
            await client.get(f"{base.rstrip('/')}/health")
            for base in (api_url, web_url)
        ]
        for response in responses:
            _ = response.raise_for_status()
    return {
        "fixture_row_count": FIXTURE_ROWS,
        "fixture_title_body_utf8_bytes": FIXTURE_BYTES,
        "relation_bytes_before_search": before,
        "relation_bytes_after_search": after,
        "raw_measured_amplification": (after - before) / FIXTURE_BYTES,
        "instrumented_http_calls": len(responses),
        "page_request_equivalent": 10_000,
    }


async def db_now(database_url: str) -> datetime:
    """Read transaction time exactly once from a read-only transaction."""
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as raw_connection:
            connection = await raw_connection.execution_options(
                isolation_level="REPEATABLE READ"
            )
            async with connection.begin():
                _ = await connection.execute(text("SET TRANSACTION READ ONLY"))
                value = await connection.scalar(
                    select(
                        literal_column(
                            "transaction_timestamp()", DateTime(timezone=True)
                        )
                    )
                )
    finally:
        await engine.dispose()
    if not isinstance(value, datetime):
        raise GateHoldError("database time is unavailable")
    return value.astimezone(UTC)


def provenance(expected_sha: str, command_manifest: Path | None = None) -> JsonObject:
    """Record tool, fixture, command, and optional complete CI provenance."""
    result: JsonObject = {
        "reviewed_sha": expected_sha,
        "python_version": platform.python_version(),
        "sqlalchemy_version": sqlalchemy.__version__,
        "orjson_version": orjson.__version__,
    }
    if command_manifest is not None:
        result["command_manifest_sha256"] = sha256_hex(command_manifest.read_bytes())
        result["fixture_sha256"] = sha256_hex(
            b"".join(
                title.encode() + b"\0" + body.encode() for title, body in fixture_rows()
            )
        )
    return result
