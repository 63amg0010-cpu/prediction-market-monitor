"""Fail-closed raw cleanup and retained report expiry policy."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, assert_never, override

from app.domain.enums import (
    ManifestItemKind,
    TombstoneDeletionReason,
    TombstoneEntityKind,
)
from app.services.configuration.canonical import canonical_bytes

from .coverage import SourceCoverage
from .formula import ManifestCorruptError, project_report
from .inputs import ReportRecord
from .manifest import ManifestIntegrityError, read_manifest
from .retention_types import (
    CleanupOutcome,
    CleanupRequest,
    ExpiryOutcome,
    ManifestItemReference,
    RetainedManifest,
    SourceEntity,
    TombstoneCandidate,
    TombstoneIdentity,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from .manifest_schema import ReportInputManifest
    from .retention_ports import RetentionRepository

RAW_RETENTION_DAYS = 30


class RetentionIntegrityError(Exception):
    """Mutable typed error so async context managers can attach tracebacks."""

    __slots__: tuple[str, ...] = ("reason",)

    reason: str

    def __init__(self, reason: str) -> None:
        """Initialize the error with a stable reason code."""
        super().__init__(reason)
        self.reason = reason

    @override
    def __str__(self) -> str:
        """Return the stable transaction-aborting reason code."""
        return self.reason


type ManifestValueSlice = ReportRecord | SourceCoverage


def manifest_value_slice(
    payload: ReportInputManifest,
    reference: ManifestItemReference,
) -> ManifestValueSlice:
    """Select the exact canonical item addressed by role and ordinal."""
    match reference.item_kind:
        case ManifestItemKind.RECORD:
            selected: tuple[ManifestValueSlice, ...] = tuple(
                item
                for item in payload.records
                if item.role is reference.role and item.ordinal == reference.ordinal
            )
        case ManifestItemKind.SOURCE_COVERAGE:
            role_coverage = tuple(
                item for item in payload.source_coverage if item.role is reference.role
            )
            selected = (
                (role_coverage[reference.ordinal],)
                if 0 <= reference.ordinal < len(role_coverage)
                else ()
            )
        case _:
            assert_never(reference.item_kind)
    if len(selected) != 1:
        reason = "manifest_value_slice_missing"
        raise RetentionIntegrityError(reason)
    return selected[0]


def _record_hash(
    record: ReportRecord,
    entity_kind: TombstoneEntityKind,
    entity_id: UUID,
) -> str | None:
    match entity_kind:
        case TombstoneEntityKind.POST_VERSION:
            return (
                record.post_content_hash
                if record.post_version_id == entity_id
                else None
            )
        case TombstoneEntityKind.ANALYSIS:
            return (
                record.analysis.output_hash
                if record.analysis.analysis_id == entity_id
                else None
            )
        case TombstoneEntityKind.MATCH:
            return next(
                (
                    item.match_hash
                    for item in record.rule_matches
                    if item.match_id == entity_id
                ),
                None,
            )
        case TombstoneEntityKind.ENGAGEMENT:
            return (
                record.engagement.engagement_hash
                if record.engagement.observation_id == entity_id
                else None
            )
        case TombstoneEntityKind.SOURCE_MANIFEST:
            return (
                record.source_publication_manifest_hash
                if record.source_publication_manifest_id == entity_id
                else None
            )
        case _:
            assert_never(entity_kind)


def _provenance_hash(
    value_slice: ManifestValueSlice,
    source: SourceEntity,
) -> str | None:
    if isinstance(value_slice, ReportRecord):
        return _record_hash(value_slice, source.entity_kind, source.id)
    if source.entity_kind is TombstoneEntityKind.SOURCE_MANIFEST:
        return (
            value_slice.cutoff_publication_manifest_hash
            if value_slice.cutoff_publication_manifest_id == source.id
            else None
        )
    return None


def _verify_reference(
    payload: ReportInputManifest,
    reference: ManifestItemReference,
    source: SourceEntity,
) -> None:
    value_slice = manifest_value_slice(payload, reference)
    value_hash = sha256(canonical_bytes(value_slice)).hexdigest()
    identities_match = (
        source.source_id is not None
        and reference.source_entity_id == source.id
        and reference.source_entity_hash == source.source_entity_hash
        and reference.source_id == source.source_id == value_slice.source_id
        and _provenance_hash(value_slice, source) == source.source_entity_hash
    )
    if not identities_match:
        reason = "source_provenance_mismatch"
        raise RetentionIntegrityError(reason)
    if value_hash != reference.value_slice_sha256:
        reason = "value_slice_hash_mismatch"
        raise RetentionIntegrityError(reason)


def _verified_manifest(repository_manifest: RetainedManifest) -> ReportInputManifest:
    try:
        payload = read_manifest(repository_manifest.envelope)
        _ = project_report(payload)
    except (ManifestIntegrityError, ManifestCorruptError) as error:
        raise RetentionIntegrityError(str(error)) from error
    return payload


async def cleanup_source(
    repository: RetentionRepository,
    request: CleanupRequest,
) -> CleanupOutcome:
    """Tombstone every retained reference before deleting an eligible source."""
    async with repository.transaction() as transaction:
        source = await transaction.lock_source(request.source_entity_id)
        if source is None:
            reason = "source_missing"
            raise RetentionIntegrityError(reason)
        eligible_at = source.retention_started_at + timedelta(days=RAW_RETENTION_DAYS)
        if request.observed_at < eligible_at:
            reason = "not_eligible"
            return CleanupOutcome(deleted=False, tombstone_ids=(), reason=reason)
        references = await transaction.lock_live_references(source.id)
        payloads: dict[UUID, tuple[ReportInputManifest, RetainedManifest]] = {}
        tombstone_ids: list[UUID] = []
        for reference in references:
            retained = await transaction.lock_manifest(reference.manifest_id)
            cached = payloads.get(reference.manifest_id)
            if cached is None:
                if retained is None:
                    reason = "retained_manifest_missing"
                    raise RetentionIntegrityError(reason)
                payload = _verified_manifest(retained)
                cached = (payload, retained)
                payloads[reference.manifest_id] = cached
            payload, retained = cached
            _verify_reference(payload, reference, source)
            tombstone = await transaction.upsert_tombstone(
                TombstoneCandidate(
                    identity=TombstoneIdentity(
                        entity_kind=source.entity_kind,
                        source_entity_id=source.id,
                        source_entity_hash=source.source_entity_hash,
                        manifest_value_slice_sha256=reference.value_slice_sha256,
                    ),
                    source_id=source.source_id,
                    published_or_observed_at=source.published_or_observed_at,
                    deleted_at=request.observed_at,
                    deletion_reason=TombstoneDeletionReason.RAW_RETENTION_EXPIRED,
                    first_manifest_id=reference.manifest_id,
                    retain_until=retained.retain_until,
                )
            )
            await transaction.switch_reference(reference.id, tombstone.id)
            tombstone_ids.append(tombstone.id)
        await transaction.delete_source(source.id)
        return CleanupOutcome(
            deleted=True,
            tombstone_ids=tuple(dict.fromkeys(tombstone_ids)),
            reason=None,
        )


async def expire_retained_reports(
    repository: RetentionRepository,
    observed_at: datetime,
) -> ExpiryOutcome:
    """Delete expired report graphs in FK-safe phases, then orphan tombstones."""
    async with repository.transaction() as transaction:
        manifests = await transaction.lock_expired_manifests(observed_at)
        for manifest in manifests:
            await transaction.delete_report_dependencies(manifest.id)
        for manifest in manifests:
            await transaction.delete_manifest_items(manifest.id)
        for manifest in manifests:
            await transaction.delete_manifest(manifest.id)
        tombstones = await transaction.lock_unreferenced_tombstones(observed_at)
        for tombstone in tombstones:
            await transaction.delete_tombstone(tombstone.id)
        return ExpiryOutcome(
            deleted_manifest_ids=tuple(item.id for item in manifests),
            deleted_tombstone_ids=tuple(item.id for item in tombstones),
        )
