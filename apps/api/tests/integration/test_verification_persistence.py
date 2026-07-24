from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.collection.verification_snapshot_identity import (
    PersistedSnapshot,
    SnapshotIntegrityError,
    VerificationSourceFactsRow,
    canonical_snapshot,
    snapshot_response,
    verified_snapshot,
)
from app.collection.verification_snapshot_queries import (
    PublicationVisibilityKey,
    first_visibility_statement,
    source_facts_statement,
)
from app.collection.verification_snapshot_store import (
    persist_snapshot,
)
from app.db.verifier_models import (
    VerificationSnapshotRecord,
    VerificationSnapshotSource,
)
from app.domain.enums import Country
from sqlalchemy.dialects import postgresql

SOURCE_ID = UUID("0c90e846-67f0-4fa8-9a22-eb2e226faab5")
RUN_ID = UUID("ae593bed-d71d-4317-88ef-7fd5893b197a")
MANIFEST_ID = UUID("3e8847bf-95dc-4c1d-809c-7debb53a4a78")
PUBLISHED_AT = datetime(2026, 7, 21, 16, 17, tzinfo=UTC)


class _SnapshotSink:
    def __init__(self) -> None:
        self.items: list[VerificationSnapshotRecord | VerificationSnapshotSource] = []

    def add(
        self, instance: VerificationSnapshotRecord | VerificationSnapshotSource
    ) -> None:
        self.items.append(instance)


def test_snapshot_persistence_restores_exact_fact_after_process_restart() -> None:
    # Given: a canonical snapshot is persisted through the production ORM writer.
    envelope = canonical_snapshot("scope-v1", PUBLISHED_AT, (_facts(),))
    sink = _SnapshotSink()
    persist_snapshot(sink, envelope)
    pending = tuple(sink.items)
    header = next(
        item for item in pending if isinstance(item, VerificationSnapshotRecord)
    )
    source = next(
        item for item in pending if isinstance(item, VerificationSnapshotSource)
    )

    # When: a new process rebuilds identity only from the durable row values.
    restored = verified_snapshot(
        PersistedSnapshot(
            snapshot_id=header.id,
            scope_version=header.scope_version,
            published_at=header.published_at,
            checksum=header.snapshot_checksum,
            canonical_payload=header.canonical_payload,
            facts=(
                VerificationSourceFactsRow.model_validate(source, from_attributes=True),
            ),
        )
    )

    # Then: timestamp, canonical bytes, checksum, and ID are byte-identical.
    assert restored == envelope


def test_snapshot_restart_rejects_tampered_canonical_payload() -> None:
    # Given: a valid header is paired with changed canonical bytes.
    envelope = canonical_snapshot("scope-v1", PUBLISHED_AT, (_facts(),))
    persisted = PersistedSnapshot(
        snapshot_id=envelope.snapshot_id,
        scope_version="scope-v1",
        published_at=PUBLISHED_AT,
        checksum=envelope.checksum,
        canonical_payload=envelope.canonical_payload + b" ",
        facts=envelope.evidence.sources,
    )

    # When/Then: restart verification rejects the mutated durable fact.
    with pytest.raises(SnapshotIntegrityError):
        _ = verified_snapshot(persisted)


def test_later_snapshot_binds_earliest_persisted_publication_visibility() -> None:
    # Given: an unchanged run was first visible before a later verifier GET.
    first_visible_at = PUBLISHED_AT
    later_published_at = PUBLISHED_AT + timedelta(minutes=15)
    facts = VerificationSourceFactsRow.model_validate(
        {
            **_facts().model_dump(),
            "publication_first_visible_at": first_visible_at,
        }
    )

    # When: the later snapshot is canonicalized and projected to the client.
    envelope = canonical_snapshot("scope-v1", later_published_at, (facts,))
    response = snapshot_response(envelope)
    changed = canonical_snapshot(
        "scope-v1",
        later_published_at,
        (
            facts.model_copy(
                update={
                    "publication_first_visible_at": first_visible_at
                    + timedelta(seconds=1)
                }
            ),
        ),
    )

    # Then: GET preserves the historical instant and it is checksum-bound.
    assert response.sources[0].publication_first_visible_at == first_visible_at
    assert changed.checksum != envelope.checksum


def test_snapshot_sql_filters_facts_without_selecting_final_free_text() -> None:
    # Given: production source and first-visibility statements.
    dialect = postgresql.dialect()

    # When: both statements are compiled with their server-derived filters.
    source_sql = str(
        source_facts_statement("scope-v1").compile(
            dialect=dialect, compile_kwargs={"literal_binds": True}
        )
    )
    visibility_sql = str(
        first_visibility_statement(
            PublicationVisibilityKey(
                SOURCE_ID,
                4,
                PUBLISHED_AT - timedelta(minutes=2),
                "scope-v1",
            )
        ).compile(dialect=dialect, compile_kwargs={"literal_binds": True})
    )

    # Then: scope/status/sequence/time stay in SQL and free text is not selected.
    assert "collection_runs.status = 'succeeded'" in source_sql
    assert "community_sources.scope_version = 'scope-v1'" in source_sql
    assert "min(verification_snapshots.published_at)" in visibility_sql
    assert "visible_publication_sequence >= 4" in visibility_sql
    assert "display_name" not in source_sql
    assert "external_key" not in source_sql
    assert "failure_code" not in source_sql


def _facts() -> VerificationSourceFactsRow:
    return VerificationSourceFactsRow(
        source_id=SOURCE_ID,
        country=Country.US,
        enabled=True,
        latest_successful_run_id=RUN_ID,
        latest_successful_run_finished_at=PUBLISHED_AT - timedelta(minutes=2),
        visible_publication_manifest_id=MANIFEST_ID,
        visible_publication_sequence=4,
        publication_first_visible_at=PUBLISHED_AT,
    )
