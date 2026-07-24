"""Atomic tombstone upsert and restrictive live-reference switching."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never
from uuid import uuid4

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert

from app.db.manifest_models import ReportInputManifestItem
from app.db.tombstone_models import (
    ReportInputManifestItemMatch,
    ReportInputManifestItemTombstone,
    ReportInputTombstone,
)
from app.domain.enums import TombstoneEntityKind

from .retention_types import (
    RetentionTombstone,
    TombstoneCandidate,
    TombstoneIdentity,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from .retention_sql_sources import ReferenceSwitchTarget


async def upsert_tombstone_row(
    session: AsyncSession,
    candidate: TombstoneCandidate,
) -> RetentionTombstone:
    """Create or extend one exact body-free tombstone identity."""
    statement = insert(ReportInputTombstone).values(
        id=uuid4(),
        entity_kind=candidate.identity.entity_kind,
        source_entity_id=candidate.identity.source_entity_id,
        source_entity_hash=candidate.identity.source_entity_hash,
        source_id=candidate.source_id,
        published_or_observed_at=candidate.published_or_observed_at,
        deleted_at=candidate.deleted_at,
        deletion_reason=candidate.deletion_reason,
        manifest_value_slice_sha256=(candidate.identity.manifest_value_slice_sha256),
        first_manifest_id=candidate.first_manifest_id,
        retain_until=candidate.retain_until,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_report_input_tombstone_identity",
        set_={
            "retain_until": func.greatest(
                ReportInputTombstone.retain_until,
                statement.excluded.retain_until,
            )
        },
    ).returning(ReportInputTombstone)
    row = (await session.execute(statement)).scalar_one()
    return _retention_tombstone(row)


async def switch_reference_row(
    session: AsyncSession,
    target: ReferenceSwitchTarget,
    tombstone_id: UUID,
) -> None:
    """Replace one locked restrictive live link with its tombstone link."""
    match target.entity_kind:
        case TombstoneEntityKind.POST_VERSION:
            statement = (
                update(ReportInputManifestItem)
                .where(
                    ReportInputManifestItem.id == target.manifest_item_id,
                    ReportInputManifestItem.live_post_version_id
                    == target.source_entity_id,
                )
                .values(
                    live_post_version_id=None,
                    post_version_tombstone_id=tombstone_id,
                )
                .returning(ReportInputManifestItem.id)
            )
            add_link = False
        case TombstoneEntityKind.ANALYSIS:
            statement = (
                update(ReportInputManifestItem)
                .where(
                    ReportInputManifestItem.id == target.manifest_item_id,
                    ReportInputManifestItem.live_analysis_id == target.source_entity_id,
                )
                .values(live_analysis_id=None)
                .returning(ReportInputManifestItem.id)
            )
            add_link = True
        case TombstoneEntityKind.ENGAGEMENT:
            statement = (
                update(ReportInputManifestItem)
                .where(
                    ReportInputManifestItem.id == target.manifest_item_id,
                    ReportInputManifestItem.live_engagement_observation_id
                    == target.source_entity_id,
                )
                .values(live_engagement_observation_id=None)
                .returning(ReportInputManifestItem.id)
            )
            add_link = True
        case TombstoneEntityKind.SOURCE_MANIFEST:
            statement = (
                update(ReportInputManifestItem)
                .where(
                    ReportInputManifestItem.id == target.manifest_item_id,
                    ReportInputManifestItem.live_source_publication_manifest_id
                    == target.source_entity_id,
                )
                .values(live_source_publication_manifest_id=None)
                .returning(ReportInputManifestItem.id)
            )
            add_link = True
        case TombstoneEntityKind.MATCH:
            statement = delete(ReportInputManifestItemMatch).where(
                ReportInputManifestItemMatch.id == target.reference_id,
                ReportInputManifestItemMatch.post_match_id == target.source_entity_id,
            )
            statement = statement.returning(ReportInputManifestItemMatch.id)
            add_link = True
        case _:
            assert_never(target.entity_kind)
    switched_id = (await session.execute(statement)).scalar_one_or_none()
    if switched_id is None:
        message = "retention_reference_switch_failed"
        raise RuntimeError(message)
    if add_link:
        link = (
            insert(ReportInputManifestItemTombstone)
            .values(
                id=uuid4(),
                manifest_item_id=target.manifest_item_id,
                tombstone_id=tombstone_id,
            )
            .on_conflict_do_nothing(constraint="uq_manifest_item_tombstone")
        )
        _ = await session.execute(link)


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
