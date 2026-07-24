"""Repeatable-read SQL persistence for canonical verifier snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from sqlalchemy import DateTime, func, select

from app.db.verifier_models import (
    VerificationSnapshotRecord,
    VerificationSnapshotSource,
)

from .verification import SourceVerificationFacts
from .verification_snapshot_identity import (
    SNAPSHOT_INTEGRITY_MISMATCH,
    PersistedSnapshot,
    SnapshotEnvelope,
    SnapshotIntegrityError,
    VerificationSourceFactsRow,
    canonical_snapshot,
    source_snapshot,
    verified_snapshot,
)
from .verification_snapshot_queries import (
    PublicationVisibilityKey,
    first_visibility_statement,
    source_facts_statement,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

SNAPSHOT_NOT_FOUND: Final = "verification_snapshot_not_found"


class _SnapshotSink(Protocol):
    def add(
        self, instance: VerificationSnapshotRecord | VerificationSnapshotSource
    ) -> None: ...


async def source_facts(
    session: AsyncSession,
    scope_version: str,
    published_at: datetime,
) -> tuple[VerificationSourceFactsRow, ...]:
    """Bind source rows to their durable first-publication visibility time."""
    rows = (await session.execute(source_facts_statement(scope_version))).mappings()
    facts: list[VerificationSourceFactsRow] = []
    for row in rows:
        item = VerificationSourceFactsRow.model_validate(row)
        key = _visibility_key(item, scope_version)
        first_visible = (
            None if key is None else await _persisted_first_visibility(session, key)
        )
        if key is not None and first_visible is None:
            first_visible = published_at
        facts.append(
            item.model_copy(update={"publication_first_visible_at": first_visible})
        )
    return tuple(facts)


async def database_now(session: AsyncSession) -> datetime:
    """Read the PostgreSQL wall clock inside the caller transaction."""
    clock = func.clock_timestamp(type_=DateTime(timezone=True))
    return (await session.execute(select(clock))).scalar_one()


def facts_checksum(
    scope_version: str,
    published_at: datetime,
    facts: tuple[VerificationSourceFactsRow, ...],
) -> str:
    """Hash the database timestamp and exact free-text-free source facts."""
    return canonical_snapshot(scope_version, published_at, facts).checksum


def snapshot_id(scope_version: str, checksum: str) -> UUID:
    """Return the canonical UUID for an already hashed snapshot fact."""
    return uuid5(NAMESPACE_URL, f"verification:{scope_version}:{checksum}")


def persist_snapshot(session: _SnapshotSink, envelope: SnapshotEnvelope) -> None:
    """Add one immutable header and its source facts to the current transaction."""
    session.add(
        VerificationSnapshotRecord(
            id=envelope.snapshot_id,
            scope_version=envelope.evidence.scope_version,
            published_at=envelope.evidence.published_at,
            snapshot_checksum=envelope.checksum,
            canonical_payload=envelope.canonical_payload,
        )
    )
    for item in envelope.evidence.sources:
        session.add(
            VerificationSnapshotSource(
                id=uuid4(),
                snapshot_id=envelope.snapshot_id,
                source_id=item.source_id,
                country=item.country,
                enabled=item.enabled,
                latest_successful_run_id=item.latest_successful_run_id,
                latest_successful_run_finished_at=(
                    item.latest_successful_run_finished_at
                ),
                visible_publication_manifest_id=(item.visible_publication_manifest_id),
                visible_publication_sequence=item.visible_publication_sequence,
                publication_first_visible_at=item.publication_first_visible_at,
            )
        )


async def load_snapshot(
    session: AsyncSession,
    snapshot_id_value: UUID,
    scope_version: str,
    checksum: str,
) -> SnapshotEnvelope:
    """Load and independently verify an issued snapshot after process restart."""
    record = (
        await session.execute(
            select(VerificationSnapshotRecord).where(
                VerificationSnapshotRecord.id == snapshot_id_value,
                VerificationSnapshotRecord.scope_version == scope_version,
                VerificationSnapshotRecord.snapshot_checksum == checksum,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise SnapshotIntegrityError(SNAPSHOT_NOT_FOUND)
    rows = tuple(
        (
            await session.execute(
                select(VerificationSnapshotSource)
                .where(VerificationSnapshotSource.snapshot_id == record.id)
                .order_by(VerificationSnapshotSource.source_id)
            )
        )
        .scalars()
        .all()
    )
    facts = tuple(
        VerificationSourceFactsRow.model_validate(row, from_attributes=True)
        for row in rows
    )
    return verified_snapshot(
        PersistedSnapshot(
            snapshot_id=record.id,
            scope_version=record.scope_version,
            published_at=record.published_at,
            checksum=record.snapshot_checksum,
            canonical_payload=record.canonical_payload,
            facts=facts,
        )
    )


async def observation_facts(
    session: AsyncSession, envelope: SnapshotEnvelope
) -> tuple[SourceVerificationFacts, ...]:
    """Derive P from the first persisted snapshot that exposed each sequence."""
    facts: list[SourceVerificationFacts] = []
    for item in envelope.evidence.sources:
        key = _visibility_key(item, envelope.evidence.scope_version)
        first_visible = (
            None if key is None else await _persisted_first_visibility(session, key)
        )
        if first_visible != item.publication_first_visible_at:
            raise SnapshotIntegrityError(SNAPSHOT_INTEGRITY_MISMATCH)
        facts.append(
            SourceVerificationFacts(
                source_id=item.source_id,
                enabled=item.enabled,
                snapshot_published_at=envelope.evidence.published_at,
                latest_successful_run_id=item.latest_successful_run_id,
                latest_successful_run_finished_at=(
                    item.latest_successful_run_finished_at
                ),
                visible_publication_manifest_id=(item.visible_publication_manifest_id),
                visible_publication_sequence=item.visible_publication_sequence,
                publication_first_visible_at=first_visible,
            )
        )
    return tuple(facts)


def _visibility_key(
    item: VerificationSourceFactsRow, scope_version: str
) -> PublicationVisibilityKey | None:
    sequence = item.visible_publication_sequence
    finished_at = item.latest_successful_run_finished_at
    if sequence is None or finished_at is None:
        return None
    return PublicationVisibilityKey(
        item.source_id,
        sequence,
        finished_at,
        scope_version,
    )


async def _persisted_first_visibility(
    session: AsyncSession, key: PublicationVisibilityKey
) -> datetime | None:
    return (await session.execute(first_visibility_statement(key))).scalar_one()


__all__ = (
    "VerificationSourceFactsRow",
    "canonical_snapshot",
    "database_now",
    "facts_checksum",
    "first_visibility_statement",
    "load_snapshot",
    "observation_facts",
    "persist_snapshot",
    "snapshot_id",
    "source_facts",
    "source_facts_statement",
    "source_snapshot",
)
