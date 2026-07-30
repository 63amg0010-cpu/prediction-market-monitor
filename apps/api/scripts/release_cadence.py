"""Pure/injected orchestration for the Todo 11 cadence contract."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

from apps.api.scripts.release_cadence_models import (
    AcceptancePhase,
    AttemptOutcome,
    CadenceAttempt,
    CadenceEpoch,
    CadenceError,
    CadenceReport,
    CadenceSlot,
    RenewalResult,
    SourceSubreceipt,
)
from apps.api.scripts.release_cadence_slots import (
    EXPECTED_COUNTS,
    attempt_rejection,
    cadence_epoch_digest,
    materialize_epoch,
    retry_allowed,
)
from apps.api.scripts.release_cadence_store import (
    CadenceStore,
    InMemoryCadenceStore,
)


def record_attempt(
    store: CadenceStore,
    epoch: CadenceEpoch,
    attempt: CadenceAttempt,
) -> AttemptOutcome:
    """Retain an attempt and atomically accept at most one success per slot."""
    slot = store.slot(
        epoch.epoch_id,
        attempt.schedule_kind,
        attempt.slot_key,
    )
    if slot is None:
        rejection = (
            "epoch_mismatch"
            if attempt.epoch_id != epoch.epoch_id
            else "slot_not_materialized"
        )
        return store.commit_attempt(
            attempt,
            rejection,
            retry_permitted=False,
        )
    rejection = attempt_rejection(epoch, slot, attempt)
    retry = (
        retry_allowed(slot, attempt, rejection) if rejection is not None else False
    )
    return store.commit_attempt(
        attempt,
        rejection,
        retry_permitted=retry,
    )


def evaluate_cadence(
    store: CadenceStore,
    epoch: CadenceEpoch,
    *,
    phase: AcceptancePhase,
    db_now: datetime,
    prior_status: CadenceReport | None = None,
) -> CadenceReport:
    """Refresh counts from durable state; day-zero can never complete."""
    observed = _utc(db_now)
    slots = store.slots(epoch.epoch_id)
    collection = tuple(item for item in slots if item.schedule_kind == "collection")
    verifier = tuple(item for item in slots if item.schedule_kind == "verifier")
    accepted_collection = _accepted_count(store, collection)
    accepted_verifier = _accepted_count(store, verifier)
    status = "HOLD"
    reason = "missing_accepted_slots"
    if phase is AcceptancePhase.STATUS:
        status = "OPERATIONAL_PENDING_CADENCE"
        reason = "day_zero_never_complete"
    elif prior_status is None:
        reason = "prior_status_required"
    elif (
        prior_status.phase is not AcceptancePhase.STATUS
        or prior_status.epoch_id != epoch.epoch_id
        or prior_status.expected_source_ids != epoch.expected_source_ids
    ):
        reason = "prior_status_mismatch"
    elif epoch.invalidated_at is not None:
        reason = "epoch_invalidated"
    elif observed < epoch.closes_at:
        reason = "epoch_open"
    elif len(collection) != EXPECTED_COUNTS["collection"] or len(
        verifier
    ) != EXPECTED_COUNTS["verifier"]:
        reason = "slot_cardinality_invalid"
    elif (
        accepted_collection == EXPECTED_COUNTS["collection"]
        and accepted_verifier == EXPECTED_COUNTS["verifier"]
    ):
        status = "COMPLETE"
        reason = "complete"
    return CadenceReport(
        phase=phase,
        epoch_id=epoch.epoch_id,
        expected_source_ids=epoch.expected_source_ids,
        expected_collection_slots=EXPECTED_COUNTS["collection"],
        expected_verifier_slots=EXPECTED_COUNTS["verifier"],
        accepted_collection_slots=accepted_collection,
        accepted_verifier_slots=accepted_verifier,
        status=status,
        reason=reason,
        observed_at=observed,
    )


def renew_epoch(  # noqa: PLR0913
    old_epoch: CadenceEpoch,
    old_slots: tuple[CadenceSlot, ...],
    *,
    db_now: datetime,
    old_expires_at: datetime,
    old_recheck_at: datetime,
    new_scope_sha256: str,
    new_source_ids: tuple[UUID, ...],
    revoked: bool,
    new_epoch_id: UUID | None = None,
    new_anchor_at: datetime | None = None,
) -> RenewalResult:
    """Preserve only an early equivalent renewal; otherwise rotate epochs."""
    now = _utc(db_now)
    normalized_sources = tuple(sorted(new_source_ids, key=str))
    preserved = (
        old_epoch.invalidated_at is None
        and not revoked
        and now < _utc(old_expires_at)
        and now < _utc(old_recheck_at)
        and new_scope_sha256 == old_epoch.scope_sha256
        and normalized_sources == old_epoch.expected_source_ids
    )
    if preserved:
        return RenewalResult(
            preserved=True,
            previous_epoch=old_epoch,
            epoch=old_epoch,
            slots=old_slots,
        )
    if new_epoch_id is None or new_anchor_at is None:
        error_code = "new_epoch_required"
        raise CadenceError(error_code)
    if new_epoch_id == old_epoch.epoch_id:
        error_code = "new_epoch_id_reused"
        raise CadenceError(error_code)
    anchor = _utc(new_anchor_at)
    if anchor <= now:
        error_code = "new_anchor_not_future"
        raise CadenceError(error_code)
    invalidated = replace(old_epoch, invalidated_at=now)
    epoch, slots = materialize_epoch(
        new_epoch_id,
        anchor,
        new_source_ids,
        old_epoch.binding_sha256,
        new_scope_sha256,
    )
    return RenewalResult(
        preserved=False,
        previous_epoch=invalidated,
        epoch=epoch,
        slots=slots,
    )


def _accepted_count(
    store: CadenceStore,
    slots: tuple[CadenceSlot, ...],
) -> int:
    return sum(
        store.accepted(item.epoch_id, item.schedule_kind, item.slot_key)
        for item in slots
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        error_code = "timestamp_not_timezone_aware"
        raise CadenceError(error_code)
    return value.astimezone(UTC)


__all__ = (
    "AcceptancePhase",
    "CadenceAttempt",
    "CadenceError",
    "InMemoryCadenceStore",
    "SourceSubreceipt",
    "cadence_epoch_digest",
    "evaluate_cadence",
    "materialize_epoch",
    "record_attempt",
    "renew_epoch",
)
