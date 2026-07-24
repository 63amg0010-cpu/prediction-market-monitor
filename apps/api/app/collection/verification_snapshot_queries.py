"""SQL projections for verifier source facts and publication visibility."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select

from app.db.auth_models import CommunitySource
from app.db.publication_models import SourceRunPublicationManifest
from app.db.run_models import CollectionRun
from app.db.verifier_models import (
    VerificationSnapshotRecord,
    VerificationSnapshotSource,
)
from app.domain.enums import Country, RunStatus

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.sql import Select


@dataclass(frozen=True, slots=True)
class PublicationVisibilityKey:
    """Immutable inputs identifying the first snapshot that exposed one run."""

    source_id: UUID
    sequence: int
    run_finished_at: datetime
    scope_version: str


def source_facts_statement(
    scope_version: str,
) -> Select[
    tuple[
        UUID,
        Country,
        bool,
        UUID,
        datetime,
        UUID,
        int,
    ]
]:
    """Select verifier-safe source columns with success and scope filters in SQL."""
    run_rank = func.row_number().over(
        partition_by=CollectionRun.source_id,
        order_by=(CollectionRun.finished_at.desc(), CollectionRun.id.desc()),
    )
    latest_runs = (
        select(
            CollectionRun.id.label("latest_successful_run_id"),
            CollectionRun.source_id.label("run_source_id"),
            CollectionRun.finished_at.label("latest_successful_run_finished_at"),
            run_rank.label("run_rank"),
        )
        .where(
            CollectionRun.scope_version == scope_version,
            CollectionRun.status == RunStatus.SUCCEEDED,
            CollectionRun.finished_at.is_not(None),
        )
        .subquery()
    )
    return (
        select(
            CommunitySource.id.label("source_id"),
            CommunitySource.country,
            CommunitySource.enabled,
            latest_runs.c.latest_successful_run_id,
            latest_runs.c.latest_successful_run_finished_at,
            SourceRunPublicationManifest.id.label("visible_publication_manifest_id"),
            SourceRunPublicationManifest.sequence.label("visible_publication_sequence"),
        )
        .outerjoin(
            latest_runs,
            and_(
                latest_runs.c.run_source_id == CommunitySource.id,
                latest_runs.c.run_rank == 1,
            ),
        )
        .outerjoin(
            SourceRunPublicationManifest,
            SourceRunPublicationManifest.run_id
            == latest_runs.c.latest_successful_run_id,
        )
        .where(CommunitySource.scope_version == scope_version)
        .order_by(CommunitySource.id)
    )


def first_visibility_statement(
    key: PublicationVisibilityKey,
) -> Select[tuple[datetime]]:
    """Select the first persisted verifier issue that exposed one sequence."""
    return (
        select(func.min(VerificationSnapshotRecord.published_at))
        .join(
            VerificationSnapshotSource,
            VerificationSnapshotSource.snapshot_id == VerificationSnapshotRecord.id,
        )
        .where(
            VerificationSnapshotRecord.scope_version == key.scope_version,
            VerificationSnapshotSource.source_id == key.source_id,
            VerificationSnapshotSource.visible_publication_sequence >= key.sequence,
            VerificationSnapshotRecord.published_at >= key.run_finished_at,
        )
    )


__all__ = (
    "PublicationVisibilityKey",
    "first_visibility_statement",
    "source_facts_statement",
)
