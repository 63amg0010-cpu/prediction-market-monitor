"""Alembic environment for async PostgreSQL and deterministic offline SQL."""

import os
from logging.config import fileConfig
from typing import override

import anyio
from alembic import context
from app.db.models import metadata as target_metadata
from sqlalchemy import Connection, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config
if config.config_file_name is not None and config.file_config.has_section("loggers"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


class MigrationDatabaseUrlMissingError(RuntimeError):
    """Raised when an online migration has no direct/session database URL."""

    @override
    def __str__(self) -> str:
        """Return a stable operator-facing configuration error."""
        return "DATABASE_URL is required for online migrations"


def _database_url() -> str | None:
    return (
        os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
        or None
    )


def run_migrations_offline() -> None:
    """Emit PostgreSQL SQL without opening a database connection."""
    context.configure(
        url=_database_url() or "postgresql+asyncpg://",
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_sync_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations(url: str) -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_run_sync_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run through the configured direct or Supavisor session-mode async URL."""
    url = _database_url()
    if url is None:
        raise MigrationDatabaseUrlMissingError
    anyio.run(_run_async_migrations, url)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
