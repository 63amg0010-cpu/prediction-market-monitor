"""Exact UTC slot materialization and attempt validation."""

from __future__ import annotations

from datetime import UTC, timedelta
from functools import cache
from hashlib import sha256
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

from apps.api.scripts.release_cadence_models import (
    CadenceAttempt,
    CadenceEpoch,
    CadenceError,
    CadenceSlot,
    ScheduleKind,
)

WINDOW: Final = timedelta(days=30)
EXPECTED_SOURCE_COUNT: Final = 2
SHA256_LENGTH: Final = 64
EXPECTED_COUNTS: Final = {"collection": 240, "verifier": 2880}
START_LIMITS: Final = {
    "collection": timedelta(minutes=30),
    "verifier": timedelta(minutes=5),
}
COMPLETION_LIMITS: Final = {
    "collection": timedelta(minutes=36),
    "verifier": timedelta(minutes=8),
}


def materialize_epoch(
    epoch_id: UUID,
    anchor_at: datetime,
    source_ids: tuple[UUID, ...],
    binding_sha256: str,
    scope_sha256: str,
) -> tuple[CadenceEpoch, tuple[CadenceSlot, ...]]:
    """Freeze two sources and materialize the exact half-open 30-day window."""
    anchor = _utc_anchor(anchor_at)
    sources = _sources(source_ids)
    _hash(binding_sha256, "binding_sha256_invalid")
    _hash(scope_sha256, "scope_sha256_invalid")
    epoch = CadenceEpoch(
        epoch_id=epoch_id,
        anchor_at=anchor,
        closes_at=anchor + WINDOW,
        epoch_sha256=cadence_epoch_digest(
            epoch_id,
            anchor,
            sources,
            binding_sha256,
            scope_sha256,
        ),
        expected_source_ids=sources,
        binding_sha256=binding_sha256,
        scope_sha256=scope_sha256,
    )
    slots = tuple(
        sorted(
            (*_collection_slots(epoch), *_verifier_slots(epoch)),
            key=lambda item: (item.due_at, item.schedule_kind),
        )
    )
    counts = {
        kind: sum(item.schedule_kind == kind for item in slots)
        for kind in EXPECTED_COUNTS
    }
    if counts != EXPECTED_COUNTS:
        error_code = "slot_cardinality_invalid"
        raise CadenceError(error_code)
    return epoch, slots


def attempt_rejection(
    epoch: CadenceEpoch,
    slot: CadenceSlot,
    attempt: CadenceAttempt,
) -> str | None:
    """Return a stable rejection or mark an attempt eligible for CAS."""
    identity_rejection = _identity_rejection(epoch, slot, attempt)
    if identity_rejection is not None:
        return identity_rejection
    source_rejection = _source_rejection(epoch, attempt)
    if source_rejection is not None:
        return source_rejection
    return _time_rejection(slot, attempt)


def _identity_rejection(
    epoch: CadenceEpoch,
    slot: CadenceSlot,
    attempt: CadenceAttempt,
) -> str | None:
    checks = (
        (epoch.invalidated_at is not None, "epoch_invalidated"),
        (attempt.epoch_id != epoch.epoch_id, "epoch_mismatch"),
        (attempt.schedule_kind != slot.schedule_kind, "schedule_kind_mismatch"),
        (attempt.slot_key != slot.slot_key, "slot_key_mismatch"),
        (attempt.mode != "schedule", "manual_mode_excluded"),
        (attempt.epoch_sha256 != epoch.epoch_sha256, "epoch_hash_mismatch"),
        (attempt.binding_sha256 != epoch.binding_sha256, "binding_mismatch"),
        (attempt.scope_sha256 != epoch.scope_sha256, "scope_mismatch"),
    )
    return next((code for failed, code in checks if failed), None)


def _source_rejection(
    epoch: CadenceEpoch,
    attempt: CadenceAttempt,
) -> str | None:
    received = tuple(item.source_id for item in attempt.source_subreceipts)
    if (
        len(set(received)) != EXPECTED_SOURCE_COUNT
        or set(received) != set(epoch.expected_source_ids)
    ):
        return "source_set_mismatch"
    if not all(item.succeeded for item in attempt.source_subreceipts):
        return "source_failed"
    return None


def _time_rejection(
    slot: CadenceSlot,
    attempt: CadenceAttempt,
) -> str | None:
    if not _aware(attempt.started_at) or not _aware(attempt.completed_at):
        return "attempt_timestamp_invalid"
    start = attempt.started_at.astimezone(UTC)
    completion = attempt.completed_at.astimezone(UTC)
    if start < slot.due_at:
        return "started_early"
    if start >= slot.due_at + START_LIMITS[slot.schedule_kind]:
        return "started_late"
    if completion < start:
        return "completed_before_start"
    if completion >= start + COMPLETION_LIMITS[slot.schedule_kind]:
        return "completed_late"
    return None


def retry_allowed(slot: CadenceSlot, attempt: CadenceAttempt, reason: str) -> bool:
    """Allow a fresh retry only while the original start window remains."""
    if reason in {
        "duplicate_after_acceptance",
        "duplicate_attempt",
        "manual_mode_excluded",
        "epoch_invalidated",
        "started_late",
    }:
        return False
    if not _aware(attempt.completed_at):
        return False
    limit = slot.due_at + START_LIMITS[slot.schedule_kind]
    return attempt.completed_at.astimezone(UTC) < limit


def _collection_slots(epoch: CadenceEpoch) -> list[CadenceSlot]:
    cursor = epoch.anchor_at.replace(minute=17)
    while cursor < epoch.anchor_at or cursor.hour % 3:
        cursor += timedelta(hours=1)
        cursor = cursor.replace(minute=17)
    return _walk(epoch, "collection", cursor, timedelta(hours=3))


def _verifier_slots(epoch: CadenceEpoch) -> list[CadenceSlot]:
    return _walk(epoch, "verifier", epoch.anchor_at, timedelta(minutes=15))


def _walk(
    epoch: CadenceEpoch,
    kind: ScheduleKind,
    cursor: datetime,
    interval: timedelta,
) -> list[CadenceSlot]:
    result: list[CadenceSlot] = []
    while cursor < epoch.closes_at:
        result.append(
            CadenceSlot(epoch.epoch_id, kind, _slot_key(cursor), cursor)
        )
        cursor += interval
    return result


def _slot_key(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


@cache
def cadence_epoch_digest(
    epoch_id: UUID,
    anchor_at: datetime,
    source_ids: tuple[UUID, UUID],
    binding_sha256: str,
    scope_sha256: str,
) -> str:
    """Hash every frozen epoch dimension in deterministic UTC order."""
    anchor = anchor_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    material = "|".join(
        (
            str(epoch_id),
            anchor,
            *(str(item) for item in source_ids),
            binding_sha256,
            scope_sha256,
        )
    )
    return sha256(material.encode()).hexdigest()


def _utc_anchor(value: datetime) -> datetime:
    if not _aware(value):
        error_code = "anchor_not_timezone_aware"
        raise CadenceError(error_code)
    result = value.astimezone(UTC)
    if result.minute or result.second or result.microsecond:
        error_code = "anchor_not_schedule_aligned"
        raise CadenceError(error_code)
    return result


def _sources(values: tuple[UUID, ...]) -> tuple[UUID, UUID]:
    if (
        len(values) != EXPECTED_SOURCE_COUNT
        or len(set(values)) != EXPECTED_SOURCE_COUNT
    ):
        error_code = "expected_source_set_invalid"
        raise CadenceError(error_code)
    ordered = sorted(values, key=str)
    return ordered[0], ordered[1]


def _hash(value: str, code: str) -> None:
    if len(value) != SHA256_LENGTH or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise CadenceError(code)


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = (
    "EXPECTED_COUNTS",
    "attempt_rejection",
    "cadence_epoch_digest",
    "materialize_epoch",
    "retry_allowed",
)
