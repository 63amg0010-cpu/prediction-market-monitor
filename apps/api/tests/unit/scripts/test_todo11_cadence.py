from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from apps.api.scripts.release_cadence import (
    AcceptancePhase,
    CadenceAttempt,
    CadenceError,
    InMemoryCadenceStore,
    SourceSubreceipt,
    cadence_epoch_digest,
    evaluate_cadence,
    materialize_epoch,
    record_attempt,
)

ANCHOR = datetime(2026, 8, 1, tzinfo=UTC)
SOURCES = (
    UUID("11111111-1111-4111-8111-111111111111"),
    UUID("22222222-2222-4222-8222-222222222222"),
)
BINDING = "a" * 64
SCOPE = "b" * 64
US = timedelta(microseconds=1)


def _attempt(  # noqa: PLR0913
    epoch_id: UUID,
    kind: str,
    due: datetime,
    *,
    start: datetime | None = None,
    completion: datetime | None = None,
    successes: tuple[bool, bool] = (True, True),
    mode: str = "schedule",
    binding: str = BINDING,
    scope: str = SCOPE,
) -> CadenceAttempt:
    started = start or due
    completed = completion or started + timedelta(minutes=1)
    epoch_hash = cadence_epoch_digest(
        epoch_id, ANCHOR, SOURCES, BINDING, SCOPE
    )
    return CadenceAttempt(
        attempt_id=uuid4(),
        epoch_id=epoch_id,
        schedule_kind=kind,
        slot_key=due.strftime("%Y-%m-%dT%H:%M:%SZ"),
        mode=mode,
        started_at=started,
        completed_at=completed,
        epoch_sha256=epoch_hash,
        binding_sha256=binding,
        scope_sha256=scope,
        source_subreceipts=tuple(
            SourceSubreceipt(
                source_id=source,
                succeeded=success,
                receipt_sha256="d" * 64,
            )
            for source, success in zip(SOURCES, successes, strict=True)
        ),
    )


def test_materializes_fixture_exact_utc_half_open_contract() -> None:
    fixture = Path(
        "apps/api/tests/fixtures/release-gate/todo11-cadence-contract-v1.json"
    )
    contract = cast("dict[str, object]", json.loads(fixture.read_text("utf-8")))
    schedules = cast("dict[str, dict[str, object]]", contract["schedules"])
    epoch, slots = materialize_epoch(uuid4(), ANCHOR, SOURCES, BINDING, SCOPE)
    collection = [item for item in slots if item.schedule_kind == "collection"]
    verifier = [item for item in slots if item.schedule_kind == "verifier"]
    assert epoch.expected_source_ids == SOURCES
    assert epoch.closes_at == ANCHOR + timedelta(days=30)
    assert len(collection) == schedules["collection"]["expected_slots"] == 240
    assert len(verifier) == schedules["verifier"]["expected_slots"] == 2880
    assert (collection[0].slot_key, collection[-1].slot_key) == (
        "2026-08-01T00:17:00Z",
        "2026-08-30T21:17:00Z",
    )
    assert (verifier[0].slot_key, verifier[-1].slot_key) == (
        "2026-08-01T00:00:00Z",
        "2026-08-30T23:45:00Z",
    )
    assert all(ANCHOR <= item.due_at < epoch.closes_at for item in slots)


@pytest.mark.parametrize(
    ("kind", "start_delta", "completion_delta", "reason"),
    [
        ("collection", -US, timedelta(), "started_early"),
        ("collection", timedelta(), timedelta(), "accepted"),
        ("collection", timedelta(minutes=30) - US, timedelta(), "accepted"),
        ("collection", timedelta(minutes=30), timedelta(), "started_late"),
        ("collection", timedelta(), -US, "completed_before_start"),
        ("collection", timedelta(), timedelta(minutes=36) - US, "accepted"),
        ("collection", timedelta(), timedelta(minutes=36), "completed_late"),
        ("verifier", timedelta(minutes=5) - US, timedelta(), "accepted"),
        ("verifier", timedelta(minutes=5), timedelta(), "started_late"),
        ("verifier", timedelta(), timedelta(minutes=8) - US, "accepted"),
        ("verifier", timedelta(), timedelta(minutes=8), "completed_late"),
    ],
)
def test_exact_microsecond_time_bounds(
    kind: str,
    start_delta: timedelta,
    completion_delta: timedelta,
    reason: str,
) -> None:
    epoch, slots = materialize_epoch(uuid4(), ANCHOR, SOURCES, BINDING, SCOPE)
    slot = next(item for item in slots if item.schedule_kind == kind)
    store = InMemoryCadenceStore()
    store.materialize(epoch, slots)
    start = slot.due_at + start_delta
    outcome = record_attempt(
        store,
        epoch,
        _attempt(
            epoch.epoch_id,
            kind,
            slot.due_at,
            start=start,
            completion=start + completion_delta,
        ),
    )
    assert outcome.reason == reason


def test_failure_timely_retry_late_retry_and_duplicate_cas() -> None:
    epoch, slots = materialize_epoch(uuid4(), ANCHOR, SOURCES, BINDING, SCOPE)
    slot = next(item for item in slots if item.schedule_kind == "collection")
    store = InMemoryCadenceStore()
    store.materialize(epoch, slots)
    failed_attempt = _attempt(
        epoch.epoch_id, "collection", slot.due_at, successes=(True, False)
    )
    failed = record_attempt(store, epoch, failed_attempt)
    duplicate_before = record_attempt(store, epoch, failed_attempt)
    winner = record_attempt(
        store, epoch, _attempt(epoch.epoch_id, "collection", slot.due_at)
    )
    duplicate_after = record_attempt(
        store, epoch, _attempt(epoch.epoch_id, "collection", slot.due_at)
    )
    assert (failed.reason, failed.retry_permitted) == ("source_failed", True)
    assert duplicate_before.reason == "duplicate_attempt"
    assert winner.accepted
    assert duplicate_after.reason == "duplicate_after_acceptance"

    later_store = InMemoryCadenceStore()
    later_store.materialize(epoch, slots)
    _ = record_attempt(later_store, epoch, failed_attempt)
    late = record_attempt(
        later_store,
        epoch,
        _attempt(
            epoch.epoch_id,
            "collection",
            slot.due_at,
            start=slot.due_at + timedelta(minutes=30),
        ),
    )
    assert (late.reason, late.retry_permitted) == ("started_late", False)
    assert not later_store.accepted(epoch.epoch_id, "collection", slot.slot_key)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"mode": "manual-smoke"}, "manual_mode_excluded"),
        ({"scope_sha256": "c" * 64}, "scope_mismatch"),
        ({"binding_sha256": "c" * 64}, "binding_mismatch"),
        ({"source_subreceipts": ()}, "source_set_mismatch"),
    ],
)
def test_manual_wrong_binding_scope_or_source_never_counts(
    change: dict[str, object],
    reason: str,
) -> None:
    epoch, slots = materialize_epoch(uuid4(), ANCHOR, SOURCES, BINDING, SCOPE)
    slot = next(item for item in slots if item.schedule_kind == "verifier")
    store = InMemoryCadenceStore()
    store.materialize(epoch, slots)
    attempt = replace(
        _attempt(epoch.epoch_id, "verifier", slot.due_at),
        **change,
    )
    assert record_attempt(store, epoch, attempt).reason == reason
    assert not store.accepted(epoch.epoch_id, "verifier", slot.slot_key)


def test_day_zero_hold_then_closed_current_state_refresh() -> None:
    epoch, slots = materialize_epoch(uuid4(), ANCHOR, SOURCES, BINDING, SCOPE)
    store = InMemoryCadenceStore()
    store.materialize(epoch, slots)
    status = evaluate_cadence(
        store, epoch, phase=AcceptancePhase.STATUS, db_now=ANCHOR
    )
    assert (status.status, status.reason) == (
        "OPERATIONAL_PENDING_CADENCE",
        "day_zero_never_complete",
    )
    for slot in slots:
        assert record_attempt(
            store,
            epoch,
            _attempt(epoch.epoch_id, slot.schedule_kind, slot.due_at),
        ).accepted
    early = evaluate_cadence(
        store,
        epoch,
        phase=AcceptancePhase.ACCEPTANCE,
        db_now=epoch.closes_at - US,
        prior_status=status,
    )
    complete = evaluate_cadence(
        store,
        epoch,
        phase=AcceptancePhase.ACCEPTANCE,
        db_now=epoch.closes_at,
        prior_status=status,
    )
    assert early.reason == "epoch_open"
    assert (complete.status, complete.accepted_collection_slots) == ("COMPLETE", 240)
    assert complete.accepted_verifier_slots == 2880
    store.force_unaccept(epoch.epoch_id, slots[0])
    refreshed = evaluate_cadence(
        store,
        epoch,
        phase=AcceptancePhase.ACCEPTANCE,
        db_now=epoch.closes_at,
        prior_status=status,
    )
    assert (refreshed.status, refreshed.reason) == (
        "HOLD",
        "missing_accepted_slots",
    )


def test_invalid_anchor_or_source_set_holds() -> None:
    with pytest.raises(CadenceError, match="anchor_not_schedule_aligned"):
        _ = materialize_epoch(uuid4(), ANCHOR + US, SOURCES, BINDING, SCOPE)
    with pytest.raises(CadenceError, match="expected_source_set_invalid"):
        _ = materialize_epoch(
            uuid4(), ANCHOR, (SOURCES[0], SOURCES[0]), BINDING, SCOPE
        )
