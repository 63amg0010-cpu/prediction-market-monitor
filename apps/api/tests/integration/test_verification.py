from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.collection.verification import (
    SourceVerificationFacts,
    derive_source_result,
    locked_verification_cursor_statement,
    verification_clock_statement,
    verification_slots,
)
from app.collection.verification_snapshot_store import (
    VerificationSourceFactsRow,
    facts_checksum,
    snapshot_id,
    source_snapshot,
)
from app.domain.enums import (
    Country,
    VerificationStatus,
)
from sqlalchemy.dialects import postgresql

SOURCE_ID = UUID("0c90e846-67f0-4fa8-9a22-eb2e226faab5")
SLOT = datetime(2026, 7, 21, 16, 15, tzinfo=UTC)


def test_verification_uses_database_clock_and_postgresql_cursor_lock() -> None:
    # Given: the SQL statements that anchor one verifier transaction.
    dialect = postgresql.dialect()

    # When: they are compiled for the production database.
    clock_sql = str(verification_clock_statement().compile(dialect=dialect))
    cursor_sql = str(
        locked_verification_cursor_statement("scope-v1").compile(dialect=dialect)
    )

    # Then: state time is database-owned and cursor advancement is row locked.
    assert "clock_timestamp" in clock_sql
    assert "FOR UPDATE" in cursor_sql
    assert "verification_cursors.scope_version" in cursor_sql


def test_missing_verifier_slots_are_materialized_in_order() -> None:
    # Given: two fifteen-minute observations were dropped.
    last_observed = SLOT - timedelta(minutes=45)

    # When: the current expected slot is reconciled.
    slots = verification_slots(last_observed, SLOT)

    # Then: every missing slot and the target are retained chronologically.
    assert slots == (
        SLOT - timedelta(minutes=30),
        SLOT - timedelta(minutes=15),
        SLOT,
    )


def test_verifier_derives_staleness_from_snapshot_time_not_client_values() -> None:
    # Given: an enabled source whose last success is older than three hours.
    published_at = SLOT + timedelta(minutes=2)
    facts = SourceVerificationFacts(
        source_id=SOURCE_ID,
        enabled=True,
        snapshot_published_at=published_at,
        latest_successful_run_id=UUID("ae593bed-d71d-4317-88ef-7fd5893b197a"),
        latest_successful_run_finished_at=published_at - timedelta(hours=3, seconds=1),
        visible_publication_manifest_id=UUID("3e8847bf-95dc-4c1d-809c-7debb53a4a78"),
        visible_publication_sequence=4,
        publication_first_visible_at=published_at - timedelta(minutes=1),
    )

    # When: the server derives the observation using persisted timestamps.
    result = derive_source_result(facts, SLOT, SLOT + timedelta(minutes=3))

    # Then: stale collection is a failure and is never accepted as client success.
    assert result.status is VerificationStatus.FAILED
    assert result.collection_recency_seconds == 10801
    assert result.failure_code == "collection_stale"


def test_verifier_derives_publication_latency_from_first_visible_snapshot() -> None:
    # Given: a later snapshot still exposes a run first seen two minutes after finish.
    finished_at = SLOT - timedelta(minutes=4)
    facts = SourceVerificationFacts(
        source_id=SOURCE_ID,
        enabled=True,
        snapshot_published_at=SLOT + timedelta(minutes=10),
        latest_successful_run_id=UUID("ae593bed-d71d-4317-88ef-7fd5893b197a"),
        latest_successful_run_finished_at=finished_at,
        visible_publication_manifest_id=UUID("3e8847bf-95dc-4c1d-809c-7debb53a4a78"),
        visible_publication_sequence=4,
        publication_first_visible_at=finished_at + timedelta(minutes=2),
    )

    # When: the server derives P from immutable snapshot history.
    result = derive_source_result(facts, SLOT, SLOT + timedelta(minutes=1))

    # Then: P is the first-visibility latency, not current snapshot recency.
    assert result.status is VerificationStatus.PASSED
    assert result.collection_recency_seconds == 840
    assert result.publication_latency_seconds == 120


def test_verifier_snapshot_identity_binds_its_database_publication_time() -> None:
    # Given: stable persisted source, successful run, and publication evidence.
    run_id = UUID("ae593bed-d71d-4317-88ef-7fd5893b197a")
    manifest_id = UUID("3e8847bf-95dc-4c1d-809c-7debb53a4a78")
    facts = (
        VerificationSourceFactsRow(
            source_id=SOURCE_ID,
            country=Country.US,
            enabled=True,
            latest_successful_run_id=run_id,
            latest_successful_run_finished_at=SLOT - timedelta(minutes=5),
            visible_publication_manifest_id=manifest_id,
            visible_publication_sequence=4,
            publication_first_visible_at=SLOT - timedelta(minutes=4),
        ),
    )

    # When: the same persisted rows are published at two database times.
    first_checksum = facts_checksum("scope-v1", SLOT, facts)
    first = source_snapshot(facts[0], SLOT)
    second = source_snapshot(facts[0], SLOT + timedelta(minutes=1))
    second_checksum = facts_checksum("scope-v1", SLOT + timedelta(minutes=1), facts)

    changed_facts = (facts[0].model_copy(update={"visible_publication_sequence": 5}),)
    changed_checksum = facts_checksum("scope-v1", SLOT, changed_facts)

    # Then: response time and persisted source facts both change immutable identity.
    assert first.collection_recency_seconds is not None
    assert second.collection_recency_seconds == first.collection_recency_seconds + 60
    assert first_checksum != second_checksum
    assert snapshot_id("scope-v1", first_checksum) != snapshot_id(
        "scope-v1", second_checksum
    )
    assert changed_checksum != first_checksum
    assert snapshot_id("scope-v1", changed_checksum) != snapshot_id(
        "scope-v1", first_checksum
    )
