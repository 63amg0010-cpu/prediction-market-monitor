"""Database-clock bridge for the synchronous Manifold evidence CLI."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, literal_column, select
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from collections.abc import Coroutine


async def db_now(database_url: str) -> datetime:
    """Read one authoritative transaction time and release its engine."""
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            statement = select(
                literal_column("transaction_timestamp()", DateTime(timezone=True))
            )
            value = await connection.scalar(statement)
    finally:
        await engine.dispose()
    if not isinstance(value, datetime):
        message = "database did not return transaction time"
        raise TypeError(message)
    return value.astimezone(UTC)


def run_database_time(operation: Coroutine[object, object, datetime]) -> datetime:
    """Run a database-clock coroutine without replacing the caller's loop."""
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(operation)
