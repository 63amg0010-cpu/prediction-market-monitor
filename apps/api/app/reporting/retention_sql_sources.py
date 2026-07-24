"""SQL source/reference locks and provenance verification rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic resolves at runtime.
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID  # noqa: TC003 - Pydantic resolves at runtime.

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (
    ManifestItemKind,
    ReportRole,
    TombstoneEntityKind,
)

from .retention_sql_statements import (
    LOCK_ANALYSIS_REFERENCES,
    LOCK_ANALYSIS_SOURCE,
    LOCK_ENGAGEMENT_REFERENCES,
    LOCK_ENGAGEMENT_SOURCE,
    LOCK_MATCH_REFERENCES,
    LOCK_MATCH_SOURCE,
    LOCK_POST_VERSION_REFERENCES,
    LOCK_POST_VERSION_SOURCE,
    LOCK_PUBLICATION_REFERENCES,
    LOCK_PUBLICATION_SOURCE,
)
from .retention_types import (
    ManifestItemReference,
    SourceEntity,
)
from .sql_input_rows import PublicationFingerprint, publication_hash

if TYPE_CHECKING:
    from sqlalchemy import TextClause
    from sqlalchemy.ext.asyncio import AsyncSession


class _Row(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class _SourceRow(_Row):
    id: UUID
    source_entity_hash: str
    source_id: UUID
    published_or_observed_at: datetime
    retention_started_at: datetime


class _PublicationRow(_Row):
    id: UUID
    run_id: UUID
    source_id: UUID
    terminal_page_commit_id: UUID
    sequence: int
    final_chain_hash: str
    post_set_hash: str
    distinct_post_version_count: int
    zero_post: bool
    committed_at: datetime
    published_or_observed_at: datetime
    retention_started_at: datetime


class _ReferenceRow(_Row):
    reference_id: UUID
    manifest_item_id: UUID
    manifest_id: UUID
    item_kind: ManifestItemKind
    role: ReportRole
    ordinal: int
    source_id: UUID
    value_slice_sha256: str


@dataclass(frozen=True, slots=True)
class ReferenceSwitchTarget:
    """Locked database link addressed by one retention reference."""

    reference_id: UUID
    manifest_item_id: UUID
    source_entity_id: UUID
    entity_kind: TombstoneEntityKind


@dataclass(frozen=True, slots=True)
class LockedReferences:
    """Verified reference values paired with their physical switch targets."""

    references: tuple[ManifestItemReference, ...]
    targets: tuple[ReferenceSwitchTarget, ...]


_SOURCE_LOCKS: tuple[tuple[TombstoneEntityKind, TextClause], ...] = (
    (TombstoneEntityKind.POST_VERSION, LOCK_POST_VERSION_SOURCE),
    (TombstoneEntityKind.ANALYSIS, LOCK_ANALYSIS_SOURCE),
    (TombstoneEntityKind.MATCH, LOCK_MATCH_SOURCE),
    (TombstoneEntityKind.ENGAGEMENT, LOCK_ENGAGEMENT_SOURCE),
    (TombstoneEntityKind.SOURCE_MANIFEST, LOCK_PUBLICATION_SOURCE),
)

_REFERENCE_LOCKS = {
    TombstoneEntityKind.POST_VERSION: LOCK_POST_VERSION_REFERENCES,
    TombstoneEntityKind.ANALYSIS: LOCK_ANALYSIS_REFERENCES,
    TombstoneEntityKind.MATCH: LOCK_MATCH_REFERENCES,
    TombstoneEntityKind.ENGAGEMENT: LOCK_ENGAGEMENT_REFERENCES,
    TombstoneEntityKind.SOURCE_MANIFEST: LOCK_PUBLICATION_REFERENCES,
}


async def lock_source_row(
    session: AsyncSession,
    entity_id: UUID,
) -> SourceEntity | None:
    """Lock one source row and derive its immutable provenance identity."""
    for entity_kind, statement in _SOURCE_LOCKS:
        mapping = (
            (await session.execute(statement, {"entity_id": entity_id}))
            .mappings()
            .one_or_none()
        )
        if mapping is None:
            continue
        if entity_kind is TombstoneEntityKind.SOURCE_MANIFEST:
            row = _PublicationRow.model_validate(mapping)
            fingerprint = PublicationFingerprint(
                id=row.id,
                run_id=row.run_id,
                source_id=row.source_id,
                terminal_page_commit_id=row.terminal_page_commit_id,
                sequence=row.sequence,
                final_chain_hash=row.final_chain_hash,
                post_set_hash=row.post_set_hash,
                distinct_post_version_count=row.distinct_post_version_count,
                zero_post=row.zero_post,
                committed_at=row.committed_at,
            )
            source_hash = publication_hash(fingerprint)
        else:
            row = _SourceRow.model_validate(mapping)
            source_hash = row.source_entity_hash
        return SourceEntity(
            id=row.id,
            entity_kind=entity_kind,
            source_entity_hash=source_hash,
            source_id=row.source_id,
            published_or_observed_at=row.published_or_observed_at,
            retention_started_at=row.retention_started_at,
        )
    return None


async def lock_reference_rows(
    session: AsyncSession,
    source: SourceEntity,
) -> LockedReferences:
    """Lock every live manifest link and bind its source identity."""
    statement = _REFERENCE_LOCKS[source.entity_kind]
    rows = tuple(
        _ReferenceRow.model_validate(row)
        for row in (
            await session.execute(statement, {"entity_id": source.id})
        ).mappings()
    )
    references = tuple(
        ManifestItemReference(
            id=row.reference_id,
            manifest_id=row.manifest_id,
            item_kind=row.item_kind,
            role=row.role,
            ordinal=row.ordinal,
            source_id=row.source_id,
            source_entity_id=source.id,
            source_entity_hash=source.source_entity_hash,
            value_slice_sha256=row.value_slice_sha256,
            live_source_entity_id=source.id,
            tombstone_id=None,
        )
        for row in rows
    )
    targets = tuple(
        ReferenceSwitchTarget(
            reference_id=row.reference_id,
            manifest_item_id=row.manifest_item_id,
            source_entity_id=source.id,
            entity_kind=source.entity_kind,
        )
        for row in rows
    )
    return LockedReferences(references, targets)
