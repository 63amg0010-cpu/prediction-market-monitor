"""Canonical identity and integrity checks for persisted verifier snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, ClassVar, Final, override
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.api.routes.verification import (
    VerificationSnapshot,
    VerificationSourceSnapshot,
)
from app.domain.enums import Country  # noqa: TC001 - Pydantic resolves at runtime.
from app.services.configuration.canonical import canonical_bytes
from app.services.dashboard.models import OutcomeStatus

if TYPE_CHECKING:
    from datetime import datetime

SNAPSHOT_INTEGRITY_MISMATCH: Final = "verification_snapshot_integrity_mismatch"


class VerificationSourceFactsRow(BaseModel):
    """Free-text-free source facts selected from one database snapshot."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    source_id: UUID
    country: Country
    enabled: bool
    latest_successful_run_id: UUID | None
    latest_successful_run_finished_at: AwareDatetime | None
    visible_publication_manifest_id: UUID | None
    visible_publication_sequence: int | None
    publication_first_visible_at: AwareDatetime | None = None


class SnapshotEvidence(BaseModel):
    """Exact canonical server fact stored before a snapshot response is returned."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    scope_version: str
    published_at: AwareDatetime
    sources: tuple[VerificationSourceFactsRow, ...]


@dataclass(frozen=True, slots=True)
class SnapshotEnvelope:
    """Verified identity, bytes, and source facts for one issued snapshot."""

    snapshot_id: UUID
    checksum: str
    canonical_payload: bytes
    evidence: SnapshotEvidence


@dataclass(frozen=True, slots=True)
class SnapshotIntegrityError(Exception):
    """Stable rejection for absent or mutated durable snapshot facts."""

    code: str

    @override
    def __str__(self) -> str:
        """Return a bounded non-secret integrity code."""
        return self.code


@dataclass(frozen=True, slots=True)
class PersistedSnapshot:
    """Header, payload, and source rows read from durable storage."""

    snapshot_id: UUID
    scope_version: str
    published_at: datetime
    checksum: str
    canonical_payload: bytes
    facts: tuple[VerificationSourceFactsRow, ...]


def canonical_snapshot(
    scope_version: str,
    published_at: datetime,
    facts: tuple[VerificationSourceFactsRow, ...],
) -> SnapshotEnvelope:
    """Bind database publication time and sorted source facts to bytes and UUID."""
    evidence = SnapshotEvidence(
        scope_version=scope_version,
        published_at=published_at,
        sources=tuple(sorted(facts, key=lambda item: item.source_id.int)),
    )
    payload = canonical_bytes(evidence)
    checksum = sha256(payload).hexdigest()
    return SnapshotEnvelope(
        snapshot_id=uuid5(NAMESPACE_URL, f"verification:{scope_version}:{checksum}"),
        checksum=checksum,
        canonical_payload=payload,
        evidence=evidence,
    )


def verified_snapshot(persisted: PersistedSnapshot) -> SnapshotEnvelope:
    """Rebuild persisted facts after restart and reject any mismatched byte."""
    envelope = canonical_snapshot(
        persisted.scope_version, persisted.published_at, persisted.facts
    )
    if (
        envelope.snapshot_id != persisted.snapshot_id
        or envelope.checksum != persisted.checksum
        or envelope.canonical_payload != persisted.canonical_payload
    ):
        raise SnapshotIntegrityError(SNAPSHOT_INTEGRITY_MISMATCH)
    return envelope


def snapshot_response(envelope: SnapshotEnvelope) -> VerificationSnapshot:
    """Project a verified durable envelope into the no-store API response."""
    published_at = envelope.evidence.published_at
    return VerificationSnapshot(
        snapshot_id=envelope.snapshot_id,
        scope_version=envelope.evidence.scope_version,
        published_at=published_at,
        checksum=envelope.checksum,
        sources=tuple(
            source_snapshot(item, published_at) for item in envelope.evidence.sources
        ),
    )


def source_snapshot(
    item: VerificationSourceFactsRow, now: datetime
) -> VerificationSourceSnapshot:
    """Project retained source facts without inventing missing evidence."""
    finished_at = item.latest_successful_run_finished_at
    recency = None if finished_at is None else int((now - finished_at).total_seconds())
    if recency is not None and recency < 0:
        recency = None
    outcome = (
        OutcomeStatus.BLOCKED
        if not item.enabled
        else OutcomeStatus.ERROR
        if item.latest_successful_run_id is None
        else OutcomeStatus.PARTIAL
        if item.visible_publication_manifest_id is None
        else OutcomeStatus.SUCCESS
    )
    return VerificationSourceSnapshot(
        source_id=item.source_id,
        country=item.country,
        enabled=item.enabled,
        status=outcome,
        latest_successful_run_id=item.latest_successful_run_id,
        latest_successful_run_finished_at=finished_at,
        collection_recency_seconds=recency,
        visible_publication_manifest_id=item.visible_publication_manifest_id,
        visible_publication_sequence=item.visible_publication_sequence,
        publication_first_visible_at=item.publication_first_visible_at,
    )


__all__ = (
    "PersistedSnapshot",
    "SnapshotEnvelope",
    "SnapshotIntegrityError",
    "VerificationSourceFactsRow",
    "canonical_snapshot",
    "snapshot_response",
    "source_snapshot",
    "verified_snapshot",
)
