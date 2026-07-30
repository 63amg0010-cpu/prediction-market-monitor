"""Single-transaction database clock adapter for receipt-only handlers."""

# ruff: noqa: EM101, TC003, TRY004, UP047

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import TypeVar, cast

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

T = TypeVar("T")


def with_database_clock(
    database_url_env: str,
    handler: Callable[[Callable[[], datetime]], T],
) -> T:
    """Read PostgreSQL transaction time once and execute a receipt handler."""

    async def execute() -> T:
        url = os.environ.get(database_url_env)
        if not url:
            raise ValueError("database_url_environment_empty")
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                value = cast(
                    "object",
                    await connection.scalar(text("SELECT transaction_timestamp()")),
                )
                if not isinstance(value, datetime):
                    raise ValueError("database_time_invalid")
                return handler(lambda: value)
        finally:
            await engine.dispose()

    return anyio.run(execute)


__all__ = ("with_database_clock",)
