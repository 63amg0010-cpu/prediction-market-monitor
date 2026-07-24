"""Fail-closed async PostgreSQL engine and session ownership."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Self, final, override

from pydantic import SecretStr
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL_ENV: Final = "DATABASE_URL"
ASYNC_POSTGRES_DRIVER: Final = "postgresql+asyncpg"
MISSING_DATABASE_URL_ERROR: Final = "database_url_missing"
INVALID_DATABASE_URL_ERROR: Final = "database_url_invalid"
INVALID_DATABASE_DRIVER_ERROR: Final = "database_driver_invalid"

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping


@dataclass(frozen=True, slots=True)
class DatabaseConfigurationError(Exception):
    """Redacted database configuration rejection."""

    code: str

    @override
    def __str__(self) -> str:
        """Return a redacted stable configuration failure."""
        return self.code


@final
class DatabaseSessions:
    """Own a tuned async engine and non-expiring session factory."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind sessions to one explicitly configured engine."""
        self._engine = engine
        self._factory = async_sessionmaker(engine, expire_on_commit=False)

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> Self:
        """Require an explicit async PostgreSQL URL with no fallback default."""
        database_url = environment.get(DATABASE_URL_ENV)
        if database_url is None or not database_url.strip():
            raise DatabaseConfigurationError(MISSING_DATABASE_URL_ERROR)
        return cls.from_secret(SecretStr(database_url))

    @classmethod
    def from_secret(cls, database_url: SecretStr) -> Self:
        """Parse trusted secret material and build a bounded serverless pool."""
        url = _parse_async_postgres_url(database_url)
        engine = create_async_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=2,
            max_overflow=0,
            pool_timeout=5,
            connect_args={
                "statement_cache_size": 0,
                "prepared_statement_cache_size": 0,
                "timeout": 5,
            },
        )
        return cls(engine)

    @asynccontextmanager
    async def open(self) -> AsyncGenerator[AsyncSession]:
        """Yield one explicitly scoped async session."""
        async with self._factory() as session:
            yield session

    async def close(self) -> None:
        """Dispose all owned pooled connections."""
        await self._engine.dispose()


def _parse_async_postgres_url(database_url: SecretStr) -> URL:
    try:
        parsed = make_url(database_url.get_secret_value())
    except ArgumentError as error:
        raise DatabaseConfigurationError(INVALID_DATABASE_URL_ERROR) from error
    if parsed.drivername != ASYNC_POSTGRES_DRIVER or parsed.database is None:
        raise DatabaseConfigurationError(INVALID_DATABASE_DRIVER_ERROR)
    return parsed
