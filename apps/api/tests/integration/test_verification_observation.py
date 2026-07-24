from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID

import pytest
from app.api.routes.verification import (
    VerificationObservationPayload,
    VerificationSnapshot,
    VerificationSourceResult,
    VerificationSourceSnapshot,
)
from app.collection.verification import SourceVerificationFacts
from app.collection.verification_command import observation
from app.collection.verification_observation_validation import (
    reject_duplicate_observation,
    reject_snapshot_reuse,
    validated_source_results,
)
from app.collection.verification_repository import (
    raise_verification_integrity_error,
)
from app.collection.verification_snapshot_store import (
    VerificationSourceFactsRow,
    canonical_snapshot,
)
from app.domain.enums import Country, VerificationStatus
from app.services.dashboard.models import OutcomeStatus
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from app.collection.verification_snapshot_identity import SnapshotEnvelope

SOURCE_ID = UUID("0c90e846-67f0-4fa8-9a22-eb2e226faab5")
SLOT = datetime(2026, 7, 21, 16, 15, tzinfo=UTC)


class _UniqueViolationError(Exception):
    sqlstate: ClassVar[str] = "23505"


def test_same_scope_slot_observation_replay_is_always_a_conflict() -> None:
    # Given: one observation already exists for the same scope and slot.
    existing_count = 1

    # When: duplicate detection runs before payload equivalence.
    with pytest.raises(HTTPException) as captured:
        reject_duplicate_observation(existing_count)

    # Then: duplicate success is never represented by another 201 receipt.
    assert captured.value.status_code == 409


def test_snapshot_reuse_is_always_a_conflict() -> None:
    # Given: one durable snapshot-use row already consumed the GET fact.
    snapshot_was_used = True

    # When: a second POST tries to consume the same immutable snapshot.
    with pytest.raises(HTTPException) as captured:
        reject_snapshot_reuse(snapshot_was_used)

    # Then: equivalent or changed payloads both receive conflict.
    assert captured.value.status_code == 409


def test_concurrent_unique_violation_is_reported_as_conflict() -> None:
    # Given: PostgreSQL wins a concurrent unique-constraint race.
    error = IntegrityError("INSERT", {}, _UniqueViolationError())

    # When: the repository translates only SQLSTATE 23505.
    with pytest.raises(HTTPException) as captured:
        raise_verification_integrity_error(error)

    # Then: the losing verifier gets a stable duplicate response.
    assert captured.value.status_code == 409


def test_non_unique_integrity_failure_is_not_masked_as_duplicate() -> None:
    # Given: PostgreSQL reports an integrity failure other than uniqueness.
    error = IntegrityError("INSERT", {}, RuntimeError("foreign key failure"))

    # When: the repository examines the database error.
    with pytest.raises(IntegrityError) as captured:
        raise_verification_integrity_error(error)

    # Then: the original infrastructure failure remains visible.
    assert captured.value is error


def test_verifier_command_uses_persisted_first_visibility_for_publication_time() -> (
    None
):
    # Given: a later GET retains the earlier snapshot that first exposed this run.
    finished_at = SLOT - timedelta(minutes=1)
    first_visible_at = SLOT + timedelta(minutes=1)
    source = VerificationSourceSnapshot.model_validate(
        {
            "source_id": str(SOURCE_ID),
            "country": Country.US,
            "enabled": True,
            "status": OutcomeStatus.SUCCESS,
            "latest_successful_run_id": "ae593bed-d71d-4317-88ef-7fd5893b197a",
            "latest_successful_run_finished_at": finished_at,
            "collection_recency_seconds": 660,
            "visible_publication_manifest_id": ("3e8847bf-95dc-4c1d-809c-7debb53a4a78"),
            "visible_publication_sequence": 4,
            "publication_first_visible_at": first_visible_at,
        }
    )
    snapshot = VerificationSnapshot(
        snapshot_id=UUID("23683ce1-d23e-481d-8e6b-2555933b2673"),
        scope_version="scope-v1",
        published_at=SLOT + timedelta(minutes=10),
        checksum="a" * 64,
        sources=(source,),
    )

    # When: the real verifier command builds the POST body from that GET response.
    payload = observation(
        snapshot,
        "scope-v1",
        SLOT,
        SLOT + timedelta(minutes=1),
    )

    # Then: P remains first-visible minus run-finished, not later-GET recency.
    assert payload.source_results[0].publication_latency_seconds == 120


def test_observation_accepts_action_start_before_persisted_snapshot_issue() -> None:
    # Given: the Action started after its due slot and fetched a later DB snapshot.
    snapshot, facts, payload = _observation_binding(
        action_started_at=SLOT + timedelta(minutes=1)
    )

    # When: the server recomputes every result from the persisted GET fact.
    results = validated_source_results(
        snapshot, facts, payload, SLOT + timedelta(minutes=3)
    )

    # Then: the original scheduler start is accepted without replacing GET time.
    assert results == payload.source_results


def test_observation_rejects_recomputed_source_result_time() -> None:
    # Given: a client changes C while retaining an otherwise valid snapshot identity.
    snapshot, facts, payload = _observation_binding(
        action_started_at=SLOT + timedelta(minutes=1)
    )
    tampered = payload.model_copy(
        update={
            "source_results": (
                payload.source_results[0].model_copy(
                    update={"collection_recency_seconds": 181}
                ),
            )
        }
    )

    # When: the changed client projection crosses the persistence boundary.
    with pytest.raises(HTTPException) as captured:
        _ = validated_source_results(
            snapshot, facts, tampered, SLOT + timedelta(minutes=3)
        )

    # Then: source-result tampering is an exact snapshot conflict.
    assert captured.value.status_code == 409


@pytest.mark.parametrize(
    "action_started_at",
    [SLOT - timedelta(seconds=1), SLOT + timedelta(minutes=2, seconds=1)],
)
def test_observation_rejects_action_time_outside_slot_to_snapshot_window(
    action_started_at: datetime,
) -> None:
    # Given: action time falls before the slot or after the persisted GET issue time.
    snapshot, facts, payload = _observation_binding(action_started_at=action_started_at)

    # When: the server validates the immutable timing window.
    with pytest.raises(HTTPException) as captured:
        _ = validated_source_results(
            snapshot, facts, payload, SLOT + timedelta(minutes=3)
        )

    # Then: replayed or impossible scheduler time is a conflict.
    assert captured.value.status_code == 409


def _observation_binding(
    action_started_at: datetime,
) -> tuple[
    SnapshotEnvelope,
    tuple[SourceVerificationFacts, ...],
    VerificationObservationPayload,
]:
    run_id = UUID("ae593bed-d71d-4317-88ef-7fd5893b197a")
    manifest_id = UUID("3e8847bf-95dc-4c1d-809c-7debb53a4a78")
    published_at = SLOT + timedelta(minutes=2)
    row = VerificationSourceFactsRow(
        source_id=SOURCE_ID,
        country=Country.US,
        enabled=True,
        latest_successful_run_id=run_id,
        latest_successful_run_finished_at=SLOT - timedelta(minutes=1),
        visible_publication_manifest_id=manifest_id,
        visible_publication_sequence=4,
        publication_first_visible_at=SLOT + timedelta(minutes=1),
    )
    snapshot = canonical_snapshot("scope-v1", published_at, (row,))
    facts = (
        SourceVerificationFacts(
            source_id=SOURCE_ID,
            enabled=True,
            snapshot_published_at=published_at,
            latest_successful_run_id=run_id,
            latest_successful_run_finished_at=SLOT - timedelta(minutes=1),
            visible_publication_manifest_id=manifest_id,
            visible_publication_sequence=4,
            publication_first_visible_at=SLOT + timedelta(minutes=1),
        ),
    )
    scheduler_latency = max(0, int((action_started_at - SLOT).total_seconds()))
    result = VerificationSourceResult(
        source_id=SOURCE_ID,
        scheduler_latency_seconds=scheduler_latency,
        collection_recency_seconds=180,
        publication_latency_seconds=120,
        status=VerificationStatus.PASSED,
        failure_code=None,
    )
    return (
        snapshot,
        facts,
        VerificationObservationPayload(
            scope_version="scope-v1",
            expected_slot_utc=SLOT,
            action_started_at=action_started_at,
            snapshot_id=snapshot.snapshot_id,
            snapshot_checksum=snapshot.checksum,
            source_results=(result,),
        ),
    )
