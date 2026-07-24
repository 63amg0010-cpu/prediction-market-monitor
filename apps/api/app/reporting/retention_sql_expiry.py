"""FK-safe expiry of retained report graphs and orphan tombstones."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update

from app.db.manifest_models import (
    ReportInputManifest as StoredManifest,
)
from app.db.manifest_models import (
    ReportInputManifestItem,
)
from app.db.report_models import DailyReport, DailyReportVersion
from app.db.tombstone_models import (
    ReportInputManifestItemMatch,
    ReportInputManifestItemTombstone,
)

from .manifest import ManifestEnvelope
from .retention_types import (
    RetainedManifest,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def lock_expired_manifest_rows(
    session: AsyncSession,
    observed_at: datetime,
) -> tuple[RetainedManifest, ...]:
    """Lock complete retained graphs at or beyond their expiry boundary."""
    rows = (
        await session.execute(
            select(StoredManifest)
            .where(StoredManifest.retain_until <= observed_at)
            .order_by(StoredManifest.retain_until, StoredManifest.id)
            .with_for_update()
        )
    ).scalars()
    return tuple(_retained_manifest(row) for row in rows)


async def lock_manifest_row(
    session: AsyncSession,
    manifest_id: UUID,
) -> RetainedManifest | None:
    """Lock one retained manifest required for a source switch."""
    row = (
        await session.execute(
            select(StoredManifest)
            .where(StoredManifest.id == manifest_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    return None if row is None else _retained_manifest(row)


async def delete_report_dependencies_row(
    session: AsyncSession,
    manifest_id: UUID,
) -> None:
    """Remove report pointers and version edges before manifest children."""
    version = (
        await session.execute(
            select(DailyReportVersion)
            .where(DailyReportVersion.manifest_id == manifest_id)
            .with_for_update()
        )
    ).scalar_one()
    report = (
        await session.execute(
            select(DailyReport)
            .where(DailyReport.id == version.report_id)
            .with_for_update()
        )
    ).scalar_one()
    if report.latest_version_id == version.id:
        report.latest_version_id = None
    _ = await session.execute(
        update(DailyReportVersion)
        .where(DailyReportVersion.supersedes_version_id == version.id)
        .values(supersedes_version_id=None)
    )
    _ = await session.execute(
        delete(DailyReportVersion).where(DailyReportVersion.id == version.id)
    )
    replacement = (
        await session.execute(
            select(DailyReportVersion.id)
            .where(DailyReportVersion.report_id == report.id)
            .order_by(DailyReportVersion.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if replacement is None:
        _ = await session.execute(
            delete(DailyReport).where(DailyReport.id == report.id)
        )
    elif report.latest_version_id is None:
        report.latest_version_id = replacement


async def delete_manifest_items_rows(
    session: AsyncSession,
    manifest_id: UUID,
) -> None:
    """Delete restrictive item links before their parent manifest."""
    item_ids = tuple(
        (
            await session.execute(
                select(ReportInputManifestItem.id).where(
                    ReportInputManifestItem.manifest_id == manifest_id
                )
            )
        ).scalars()
    )
    if item_ids:
        _ = await session.execute(
            delete(ReportInputManifestItemMatch).where(
                ReportInputManifestItemMatch.manifest_item_id.in_(item_ids)
            )
        )
        _ = await session.execute(
            delete(ReportInputManifestItemTombstone).where(
                ReportInputManifestItemTombstone.manifest_item_id.in_(item_ids)
            )
        )
        _ = await session.execute(
            delete(ReportInputManifestItem).where(
                ReportInputManifestItem.id.in_(item_ids)
            )
        )


async def delete_manifest_row(
    session: AsyncSession,
    manifest_id: UUID,
) -> None:
    """Delete one child-free canonical manifest payload."""
    deleted_id = (
        await session.execute(
            delete(StoredManifest)
            .where(StoredManifest.id == manifest_id)
            .returning(StoredManifest.id)
        )
    ).scalar_one_or_none()
    if deleted_id != manifest_id:
        message = "retention_manifest_delete_failed"
        raise RuntimeError(message)


def _retained_manifest(row: StoredManifest) -> RetainedManifest:
    return RetainedManifest(
        id=row.id,
        envelope=ManifestEnvelope(
            codec=row.codec,
            compressed_payload=row.compressed_payload,
            uncompressed_byte_length=row.uncompressed_byte_length,
            manifest_payload_sha256=row.manifest_payload_sha256,
            input_set_hash=row.input_set_hash,
        ),
        retain_until=row.retain_until,
    )
