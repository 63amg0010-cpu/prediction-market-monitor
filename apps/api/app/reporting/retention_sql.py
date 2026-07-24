"""Durable transaction adapter for restrictive report retention."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, final

from .retention_sql_deletion import (
    delete_source_row,
    eligible_source_ids,
)
from .retention_sql_expiry import (
    delete_manifest_items_rows,
    delete_manifest_row,
    delete_report_dependencies_row,
    lock_expired_manifest_rows,
    lock_manifest_row,
)
from .retention_sql_orphans import delete_tombstone_row, lock_orphan_tombstone_rows
from .retention_sql_sources import (
    ReferenceSwitchTarget,
    lock_reference_rows,
    lock_source_row,
)
from .retention_sql_tombstones import switch_reference_row, upsert_tombstone_row

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.session import DatabaseSessions

    from .retention_ports import RetentionTransaction
    from .retention_types import (
        ManifestItemReference,
        RetainedManifest,
        RetentionTombstone,
        SourceEntity,
        TombstoneCandidate,
    )


@final
class SqlAlchemyRetentionRepository:
    """Provide rollback-capable SQL retention transactions and bounded discovery."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        """Bind retention operations to one durable session owner."""
        self._sessions = sessions

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[RetentionTransaction]:
        """Open one atomic transaction for verification, switch, and deletion."""
        async with self._sessions.open() as session, session.begin():
            yield _SqlRetentionTransaction(session)

    async def eligible_source_ids(
        self,
        observed_at: datetime,
        limit: int,
    ) -> tuple[UUID, ...]:
        """Return one bounded dependency-first raw cleanup batch."""
        async with self._sessions.open() as session, session.begin():
            return await eligible_source_ids(session, observed_at, limit)


@final
class _SqlRetentionTransaction:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._locked_source: SourceEntity | None = None
        self._targets: dict[UUID, ReferenceSwitchTarget] = {}

    async def lock_source(self, entity_id: UUID) -> SourceEntity | None:
        source = await lock_source_row(self._session, entity_id)
        self._locked_source = source
        return source

    async def lock_live_references(
        self,
        entity_id: UUID,
    ) -> tuple[ManifestItemReference, ...]:
        source = self._locked_source
        if source is None or source.id != entity_id:
            message = "retention_source_not_locked"
            raise RuntimeError(message)
        locked = await lock_reference_rows(self._session, source)
        self._targets = {item.reference_id: item for item in locked.targets}
        return locked.references

    async def lock_manifest(
        self,
        manifest_id: UUID,
    ) -> RetainedManifest | None:
        return await lock_manifest_row(self._session, manifest_id)

    async def upsert_tombstone(
        self,
        candidate: TombstoneCandidate,
    ) -> RetentionTombstone:
        return await upsert_tombstone_row(self._session, candidate)

    async def switch_reference(
        self,
        reference_id: UUID,
        tombstone_id: UUID,
    ) -> None:
        target = self._targets.get(reference_id)
        if target is None:
            message = "retention_reference_not_locked"
            raise RuntimeError(message)
        await switch_reference_row(self._session, target, tombstone_id)

    async def delete_source(self, entity_id: UUID) -> None:
        source = self._locked_source
        if source is None or source.id != entity_id:
            message = "retention_source_not_locked"
            raise RuntimeError(message)
        await delete_source_row(self._session, source)

    async def lock_expired_manifests(
        self,
        observed_at: datetime,
    ) -> tuple[RetainedManifest, ...]:
        return await lock_expired_manifest_rows(self._session, observed_at)

    async def delete_report_dependencies(self, manifest_id: UUID) -> None:
        await delete_report_dependencies_row(self._session, manifest_id)

    async def delete_manifest_items(self, manifest_id: UUID) -> None:
        await delete_manifest_items_rows(self._session, manifest_id)

    async def delete_manifest(self, manifest_id: UUID) -> None:
        await delete_manifest_row(self._session, manifest_id)

    async def lock_unreferenced_tombstones(
        self,
        observed_at: datetime,
    ) -> tuple[RetentionTombstone, ...]:
        return await lock_orphan_tombstone_rows(self._session, observed_at)

    async def delete_tombstone(self, tombstone_id: UUID) -> None:
        await delete_tombstone_row(self._session, tombstone_id)
