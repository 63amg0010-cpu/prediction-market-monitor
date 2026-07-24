"""PostgreSQL locking and deletion of unreferenced retention tombstones."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import bindparam, delete, exists, select

from app.db.manifest_models import ReportInputManifestItem
from app.db.tombstone_models import (
    ReportInputManifestItemTombstone,
    ReportInputTombstone,
)

from .retention_types import (
    RetentionTombstone,
    TombstoneIdentity,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

_DIRECT_REFERENCE = exists().where(
    ReportInputManifestItem.post_version_tombstone_id == ReportInputTombstone.id
)
_SHARED_REFERENCE = exists().where(
    ReportInputManifestItemTombstone.tombstone_id == ReportInputTombstone.id
)
LOCK_ORPHAN_TOMBSTONES: Final = (
    select(ReportInputTombstone)
    .where(
        ReportInputTombstone.retain_until <= bindparam("observed_at"),
        ~_DIRECT_REFERENCE,
        ~_SHARED_REFERENCE,
    )
    .order_by(ReportInputTombstone.retain_until, ReportInputTombstone.id)
    .with_for_update()
)


async def lock_orphan_tombstone_rows(
    session: AsyncSession,
    observed_at: datetime,
) -> tuple[RetentionTombstone, ...]:
    """Lock elapsed tombstones absent from every manifest item link."""
    rows = (
        await session.execute(
            LOCK_ORPHAN_TOMBSTONES,
            {"observed_at": observed_at},
        )
    ).scalars()
    return tuple(_retention_tombstone(row) for row in rows)


async def delete_tombstone_row(
    session: AsyncSession,
    tombstone_id: UUID,
) -> None:
    """Delete one locked tombstone after its final reference expired."""
    deleted_id = (
        await session.execute(
            delete(ReportInputTombstone)
            .where(ReportInputTombstone.id == tombstone_id)
            .returning(ReportInputTombstone.id)
        )
    ).scalar_one_or_none()
    if deleted_id != tombstone_id:
        message = "retention_tombstone_delete_failed"
        raise RuntimeError(message)


def _retention_tombstone(row: ReportInputTombstone) -> RetentionTombstone:
    return RetentionTombstone(
        id=row.id,
        identity=TombstoneIdentity(
            entity_kind=row.entity_kind,
            source_entity_id=row.source_entity_id,
            source_entity_hash=row.source_entity_hash,
            manifest_value_slice_sha256=row.manifest_value_slice_sha256,
        ),
        source_id=row.source_id,
        published_or_observed_at=row.published_or_observed_at,
        deleted_at=row.deleted_at,
        deletion_reason=row.deletion_reason,
        first_manifest_id=row.first_manifest_id,
        retain_until=row.retain_until,
    )
