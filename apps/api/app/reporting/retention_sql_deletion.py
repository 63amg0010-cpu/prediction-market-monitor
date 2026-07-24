"""Bounded raw-source discovery and FK-safe source deletion operations."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, ClassVar, assert_never
from uuid import UUID  # noqa: TC003 - Pydantic resolves at runtime.

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, exists, select, update

from app.db.analysis_models import Analysis, AnalysisQueueItem
from app.db.page_models import PageCommitItem
from app.db.post_models import EngagementObservation, Post, PostVersion
from app.db.publication_models import SourceRunPublicationManifest
from app.db.rule_models import PostMatch
from app.domain.enums import TombstoneEntityKind

from .retention_sql_statements import ELIGIBLE_SOURCE_IDS

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from .retention_types import SourceEntity

RAW_RETENTION_DAYS = 30
MAX_RETENTION_BATCH = 1000


class _CandidateRow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    entity_id: UUID


async def eligible_source_ids(
    session: AsyncSession,
    observed_at: datetime,
    limit: int,
) -> tuple[UUID, ...]:
    """Discover an ordered bounded batch whose raw retention elapsed."""
    if not 1 <= limit <= MAX_RETENTION_BATCH:
        message = "retention_batch_limit_invalid"
        raise ValueError(message)
    cutoff = observed_at - timedelta(days=RAW_RETENTION_DAYS)
    return tuple(
        _CandidateRow.model_validate(row).entity_id
        for row in (
            await session.execute(
                ELIGIBLE_SOURCE_IDS,
                {"cutoff": cutoff, "limit": limit},
            )
        ).mappings()
    )


async def delete_source_row(
    session: AsyncSession,
    source: SourceEntity,
) -> None:
    """Delete one locked source only after all restrictive links switched."""
    match source.entity_kind:
        case TombstoneEntityKind.POST_VERSION:
            deleted_id = await _delete_post_version(session, source.id)
        case TombstoneEntityKind.ANALYSIS:
            deleted_id = (
                await session.execute(
                    delete(Analysis)
                    .where(Analysis.id == source.id)
                    .returning(Analysis.id)
                )
            ).scalar_one_or_none()
        case TombstoneEntityKind.MATCH:
            deleted_id = (
                await session.execute(
                    delete(PostMatch)
                    .where(PostMatch.id == source.id)
                    .returning(PostMatch.id)
                )
            ).scalar_one_or_none()
        case TombstoneEntityKind.ENGAGEMENT:
            deleted_id = (
                await session.execute(
                    delete(EngagementObservation)
                    .where(EngagementObservation.id == source.id)
                    .returning(EngagementObservation.id)
                )
            ).scalar_one_or_none()
        case TombstoneEntityKind.SOURCE_MANIFEST:
            deleted_id = (
                await session.execute(
                    delete(SourceRunPublicationManifest)
                    .where(SourceRunPublicationManifest.id == source.id)
                    .returning(SourceRunPublicationManifest.id)
                )
            ).scalar_one_or_none()
        case _:
            assert_never(source.entity_kind)
    if deleted_id != source.id:
        message = "retention_source_delete_failed"
        raise RuntimeError(message)


async def _delete_post_version(
    session: AsyncSession,
    version_id: UUID,
) -> UUID | None:
    post_id = (
        await session.execute(
            select(PostVersion.post_id)
            .where(PostVersion.id == version_id)
            .with_for_update()
        )
    ).scalar_one()
    _ = await session.execute(
        delete(Analysis).where(Analysis.post_version_id == version_id)
    )
    _ = await session.execute(
        delete(PostMatch).where(PostMatch.post_version_id == version_id)
    )
    _ = await session.execute(
        delete(EngagementObservation).where(
            EngagementObservation.post_version_id == version_id
        )
    )
    _ = await session.execute(
        delete(AnalysisQueueItem).where(AnalysisQueueItem.post_version_id == version_id)
    )
    _ = await session.execute(
        update(PageCommitItem)
        .where(PageCommitItem.post_version_id == version_id)
        .values(post_version_id=None)
    )
    _ = await session.execute(
        update(Post)
        .where(Post.id == post_id, Post.current_version_id == version_id)
        .values(current_version_id=None)
    )
    deleted_id = (
        await session.execute(
            delete(PostVersion)
            .where(PostVersion.id == version_id)
            .returning(PostVersion.id)
        )
    ).scalar_one_or_none()
    _ = await session.execute(
        delete(Post).where(
            Post.id == post_id,
            ~exists().where(PostVersion.post_id == post_id),
        )
    )
    return deleted_id
