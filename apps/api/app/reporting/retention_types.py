"""Immutable values used by report retention ports and policy."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.enums import (
    ManifestItemKind,
    ReportRole,
    TombstoneDeletionReason,
    TombstoneEntityKind,
)

from .manifest import ManifestEnvelope


@dataclass(frozen=True, slots=True)
class SourceEntity:
    """Body-bearing provenance row eligible for bounded raw cleanup."""

    id: UUID
    entity_kind: TombstoneEntityKind
    source_entity_hash: str
    source_id: UUID | None
    published_or_observed_at: datetime | None
    retention_started_at: datetime


@dataclass(frozen=True, slots=True)
class RetainedManifest:
    """Canonical payload and retention boundary locked during cleanup."""

    id: UUID
    envelope: ManifestEnvelope
    retain_until: datetime


@dataclass(frozen=True, slots=True)
class ManifestItemReference:
    """Restrictive live-or-tombstone link for one canonical value slice."""

    id: UUID
    manifest_id: UUID
    item_kind: ManifestItemKind
    role: ReportRole
    ordinal: int
    source_id: UUID
    source_entity_id: UUID
    source_entity_hash: str
    value_slice_sha256: str
    live_source_entity_id: UUID | None
    tombstone_id: UUID | None


@dataclass(frozen=True, slots=True)
class TombstoneIdentity:
    """Database-equivalent tombstone deduplication key."""

    entity_kind: TombstoneEntityKind
    source_entity_id: UUID
    source_entity_hash: str
    manifest_value_slice_sha256: str


@dataclass(frozen=True, slots=True)
class TombstoneCandidate:
    """Verified deletion provenance proposed for atomic create or reuse."""

    identity: TombstoneIdentity
    source_id: UUID | None
    published_or_observed_at: datetime | None
    deleted_at: datetime
    deletion_reason: TombstoneDeletionReason
    first_manifest_id: UUID
    retain_until: datetime


@dataclass(frozen=True, slots=True)
class RetentionTombstone:
    """Body-free deletion provenance retained with report references."""

    id: UUID
    identity: TombstoneIdentity
    source_id: UUID | None
    published_or_observed_at: datetime | None
    deleted_at: datetime
    deletion_reason: TombstoneDeletionReason
    first_manifest_id: UUID
    retain_until: datetime


@dataclass(frozen=True, slots=True)
class CleanupRequest:
    """One source identity and transaction observation time."""

    source_entity_id: UUID
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """Source cleanup result without exposing deleted content."""

    deleted: bool
    tombstone_ids: tuple[UUID, ...]
    reason: str | None


@dataclass(frozen=True, slots=True)
class ExpiryOutcome:
    """Expired report graphs and now-unreferenced tombstones removed."""

    deleted_manifest_ids: tuple[UUID, ...]
    deleted_tombstone_ids: tuple[UUID, ...]
