from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import pytest
from app.domain.enums import ManifestItemKind, ReportRole, TombstoneEntityKind
from app.reporting.manifest import build_manifest
from app.reporting.retention import (
    RetentionIntegrityError,
    cleanup_source,
    expire_retained_reports,
)
from app.reporting.retention_memory import InMemoryRetentionRepository
from app.reporting.retention_types import (
    CleanupRequest,
    ManifestItemReference,
    RetainedManifest,
    SourceEntity,
)
from app.services.configuration.canonical import canonical_bytes

from .factories import manifest_payload, record, valid_analysis

NOW = datetime(2026, 8, 22, tzinfo=UTC)
MANIFEST_ID = UUID(int=7000)
REFERENCE_ID = UUID(int=7001)


def retained_fixture(
    *,
    manifest_id: UUID = MANIFEST_ID,
    reference_id: UUID = REFERENCE_ID,
    retain_until: datetime | None = None,
) -> tuple[RetainedManifest, ManifestItemReference, SourceEntity]:
    source_record = record(
        1,
        ReportRole.PRIMARY,
        valid_analysis(1, relevance=True, sentiment=None),
    )
    build = build_manifest(manifest_payload((source_record,)))
    normalized = build.payload.records[0]
    value_hash = sha256(canonical_bytes(normalized)).hexdigest()
    manifest = RetainedManifest(
        id=manifest_id,
        envelope=build.envelope,
        retain_until=retain_until or NOW + timedelta(days=150),
    )
    reference = ManifestItemReference(
        id=reference_id,
        manifest_id=manifest_id,
        item_kind=ManifestItemKind.RECORD,
        role=ReportRole.PRIMARY,
        ordinal=0,
        source_id=normalized.source_id,
        source_entity_id=normalized.post_version_id,
        source_entity_hash=normalized.post_content_hash,
        value_slice_sha256=value_hash,
        live_source_entity_id=normalized.post_version_id,
        tombstone_id=None,
    )
    source = SourceEntity(
        id=normalized.post_version_id,
        entity_kind=TombstoneEntityKind.POST_VERSION,
        source_entity_hash=normalized.post_content_hash,
        source_id=normalized.source_id,
        published_or_observed_at=normalized.published_at_utc,
        retention_started_at=NOW - timedelta(days=30),
    )
    return manifest, reference, source


@pytest.mark.asyncio
async def test_cleanup_switches_reference_before_deleting_source() -> None:
    # Given: one eligible raw source referenced by a verified retained value slice.
    manifest, reference, source = retained_fixture()
    repository = InMemoryRetentionRepository(
        sources=(source,), manifests=(manifest,), references=(reference,)
    )

    # When: cleanup runs just before and exactly at the 30-day boundary.
    early = await cleanup_source(
        repository,
        CleanupRequest(
            source_entity_id=source.id,
            observed_at=NOW - timedelta(microseconds=1),
        ),
    )
    outcome = await cleanup_source(
        repository,
        CleanupRequest(source_entity_id=source.id, observed_at=NOW),
    )

    # Then: the reference is tombstoned first and the body-bearing row is gone.
    assert early.deleted is False
    switched = repository.reference(reference.id)
    assert outcome.deleted is True
    assert repository.source(source.id) is None
    assert switched is not None
    assert switched.live_source_entity_id is None
    assert switched.tombstone_id is not None
    assert repository.events[-2:] == ("reference_switched", "source_deleted")


@pytest.mark.asyncio
async def test_cleanup_fails_closed_on_missing_value_or_hash() -> None:
    # Given: a retained item whose declared value-slice hash is corrupt.
    manifest, reference, source = retained_fixture()
    corrupt = replace(
        reference,
        value_slice_sha256=f"{'0' * 63}1",
    )
    repository = InMemoryRetentionRepository(
        sources=(source,), manifests=(manifest,), references=(corrupt,)
    )

    # When: transactional cleanup verifies the retained payload and item hash.
    with pytest.raises(RetentionIntegrityError, match="value_slice_hash_mismatch"):
        _ = await cleanup_source(
            repository,
            CleanupRequest(source_entity_id=source.id, observed_at=NOW),
        )

    # Then: rollback preserves the source and live FK without a tombstone.
    unchanged = repository.reference(reference.id)
    assert repository.source(source.id) == source
    assert unchanged == corrupt
    assert repository.tombstones == ()


@pytest.mark.asyncio
async def test_shared_tombstone_lifecycle_uses_last_manifest_expiry() -> None:
    # Given: two retained manifests share the same immutable source value slice.
    first_manifest, first_reference, source = retained_fixture(
        retain_until=NOW + timedelta(days=1)
    )
    second_manifest, second_reference, _ = retained_fixture(
        manifest_id=UUID(int=7010),
        reference_id=UUID(int=7011),
        retain_until=NOW + timedelta(days=2),
    )
    repository = InMemoryRetentionRepository(
        sources=(source,),
        manifests=(first_manifest, second_manifest),
        references=(first_reference, second_reference),
    )
    _ = await cleanup_source(
        repository,
        CleanupRequest(source_entity_id=source.id, observed_at=NOW),
    )

    # When: the first and then the final retained report graph expires.
    first_expiry = await expire_retained_reports(repository, NOW + timedelta(days=1))
    second_expiry = await expire_retained_reports(repository, NOW + timedelta(days=2))

    # Then: one shared tombstone survives until its last reference disappears.
    assert first_expiry.deleted_tombstone_ids == ()
    assert second_expiry.deleted_tombstone_ids
    assert repository.tombstones == ()
    assert repository.events[-4:] == (
        "report_dependencies_deleted",
        "manifest_items_deleted",
        "manifest_deleted",
        "tombstone_deleted",
    )
