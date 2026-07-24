from dataclasses import replace
from datetime import timedelta
from uuid import UUID

import pytest
from app.reporting.retention import (
    RetentionIntegrityError,
    cleanup_source,
    expire_retained_reports,
)
from app.reporting.retention_memory import InMemoryRetentionRepository
from app.reporting.retention_types import CleanupRequest
from tests.unit.reporting.test_retention import NOW, retained_fixture


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
async def test_shared_tombstone_lifecycle() -> None:
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
