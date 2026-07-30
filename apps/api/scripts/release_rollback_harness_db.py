"""Real PostgreSQL adapter for the guarded disposable rollback harness."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.release_rollback_harness_models import DatabaseSnapshot

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

ROOT = Path(__file__).resolve().parents[3]
ALEMBIC = ROOT / "apps" / "api" / "alembic.ini"
DCINSIDE_SQL = (
    "SELECT id::text, platform::text, external_key, scope_version, enabled "
    "FROM community_sources WHERE platform::text='dcinside'"
)
MANIFOLD_SQL = (
    "SELECT enabled, "
    "(to_jsonb(source)->>'active_authorization_id') IS NULL "
    "AND (to_jsonb(source)->>'current_budget_id') IS NULL "
    "AND (to_jsonb(source)->>'current_binding_id') IS NULL "
    "AND (to_jsonb(source)->>'current_cadence_id') IS NULL AS pointers_null "
    "FROM community_sources AS source WHERE platform::text='manifold'"
)


async def _snapshot(url: str) -> DatabaseSnapshot:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            revision_value = cast(
                "object",
                await connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                ),
            )
            if not isinstance(revision_value, str):
                msg = "database_revision_invalid"
                raise TypeError(msg)
            revision = revision_value
            dcinside = cast(
                "Mapping[str, object]",
                (
                    await connection.execute(
                        text(DCINSIDE_SQL)
                    )
                ).mappings().one(),
            )
            manifold = (
                await connection.execute(
                    text(MANIFOLD_SQL)
                )
            ).mappings().one_or_none()
    finally:
        await engine.dispose()
    binding = json.dumps(
        dict(dcinside),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return DatabaseSnapshot(
        revision=revision,
        manifold_present=manifold is not None,
        manifold_enabled=bool(manifold and manifold["enabled"]),
        manifold_pointers_null=bool(manifold and manifold["pointers_null"]),
        dcinside_binding_sha256=hashlib.sha256(binding).hexdigest(),
    )


def _run(awaitable: Coroutine[object, object, DatabaseSnapshot]) -> DatabaseSnapshot:
    loop = asyncio.SelectorEventLoop()
    with asyncio.Runner(loop_factory=lambda: loop) as runner:
        return runner.run(awaitable)


class RealDatabase:
    """Alembic and read-only snapshot adapter for one already-guarded URL."""

    def snapshot(self, url: str) -> DatabaseSnapshot:
        """Read exact revision, inert Manifold state, and DCInside hash."""
        return _run(_snapshot(url))

    def migrate(self, url: str, direction: str, revision: str) -> None:
        """Run one exact real Alembic movement on the guarded URL."""
        previous = os.environ.get("MIGRATION_DATABASE_URL")
        os.environ["MIGRATION_DATABASE_URL"] = url
        try:
            config = Config(str(ALEMBIC))
            if direction == "upgrade":
                command.upgrade(config, revision)
            elif direction == "downgrade":
                command.downgrade(config, revision)
            else:
                msg = "migration_direction_invalid"
                raise ValueError(msg)
        finally:
            if previous is None:
                del os.environ["MIGRATION_DATABASE_URL"]
            else:
                os.environ["MIGRATION_DATABASE_URL"] = previous


__all__ = ("RealDatabase",)
