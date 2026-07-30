"""Injected persistence boundary and deterministic in-memory CAS store."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

from apps.api.scripts.release_cadence_models import (
    AttemptOutcome,
    CadenceAttempt,
    CadenceEpoch,
    CadenceError,
    CadenceSlot,
    ScheduleKind,
)


class CadenceStore(Protocol):
    """Atomic storage operations required by cadence domain logic."""

    def materialize(
        self,
        epoch: CadenceEpoch,
        slots: Sequence[CadenceSlot],
    ) -> None:
        """Create every unique workflow slot before the epoch begins."""
        ...

    def slot(
        self,
        epoch_id: UUID,
        schedule_kind: str,
        slot_key: str,
    ) -> CadenceSlot | None:
        """Return one exact epoch/kind/key slot."""
        ...

    def commit_attempt(
        self,
        attempt: CadenceAttempt,
        rejection: str | None,
        *,
        retry_permitted: bool,
    ) -> AttemptOutcome:
        """Atomically retain the attempt and compare-and-set acceptance."""
        ...

    def slots(self, epoch_id: UUID) -> tuple[CadenceSlot, ...]:
        """Return the materialized current state for one epoch."""
        ...

    def accepted(
        self,
        epoch_id: UUID,
        schedule_kind: ScheduleKind,
        slot_key: str,
    ) -> bool:
        """Return whether one exact workflow slot has a CAS winner."""
        ...


class InMemoryCadenceStore:
    """A transaction-shaped fake proving unique slot and acceptance CAS rules."""

    def __init__(self) -> None:
        """Initialize empty deterministic storage."""
        self._epochs: dict[UUID, CadenceEpoch] = {}
        self._slots: dict[tuple[UUID, str, str], CadenceSlot] = {}
        self._attempts: dict[tuple[UUID, str], list[AttemptOutcome]] = {}
        self._attempt_ids: dict[UUID, AttemptOutcome] = {}
        self._accepted: set[tuple[UUID, str, str]] = set()

    def materialize(
        self,
        epoch: CadenceEpoch,
        slots: Sequence[CadenceSlot],
    ) -> None:
        """Materialize idempotently or fail on any changed frozen value."""
        if epoch.epoch_id in self._epochs and self._epochs[epoch.epoch_id] != epoch:
            error_code = "epoch_conflict"
            raise CadenceError(error_code)
        incoming = {
            (item.epoch_id, item.schedule_kind, item.slot_key) for item in slots
        }
        if len(incoming) != len(slots):
            error_code = "duplicate_slot"
            raise CadenceError(error_code)
        if any(item.epoch_id != epoch.epoch_id for item in slots):
            error_code = "foreign_epoch_slot"
            raise CadenceError(error_code)
        existing = {key for key in self._slots if key[0] == epoch.epoch_id}
        if existing and existing != incoming:
            error_code = "materialized_slot_conflict"
            raise CadenceError(error_code)
        self._epochs[epoch.epoch_id] = epoch
        for item in slots:
            self._slots[(item.epoch_id, item.schedule_kind, item.slot_key)] = item

    def slot(
        self,
        epoch_id: UUID,
        schedule_kind: str,
        slot_key: str,
    ) -> CadenceSlot | None:
        """Return one exact slot without fallback matching."""
        return self._slots.get((epoch_id, schedule_kind, slot_key))

    def commit_attempt(
        self,
        attempt: CadenceAttempt,
        rejection: str | None,
        *,
        retry_permitted: bool,
    ) -> AttemptOutcome:
        """Retain one outcome and allow only the first eligible CAS winner."""
        previous = self._attempt_ids.get(attempt.attempt_id)
        if previous is not None:
            return AttemptOutcome(
                attempt_id=attempt.attempt_id,
                accepted=False,
                reason="duplicate_attempt",
                retry_permitted=previous.retry_permitted and not previous.accepted,
            )
        key = (attempt.epoch_id, attempt.schedule_kind, attempt.slot_key)
        accepted = rejection is None and key not in self._accepted
        reason = "accepted"
        if key in self._accepted:
            reason = "duplicate_after_acceptance"
        elif rejection is not None:
            reason = rejection
        if accepted:
            self._accepted.add(key)
        outcome = AttemptOutcome(
            attempt_id=attempt.attempt_id,
            accepted=accepted,
            reason=reason,
            retry_permitted=retry_permitted if not accepted else False,
        )
        self._attempt_ids[attempt.attempt_id] = outcome
        self._attempts.setdefault((attempt.epoch_id, attempt.slot_key), []).append(
            outcome
        )
        return outcome

    def slots(self, epoch_id: UUID) -> tuple[CadenceSlot, ...]:
        """Return one epoch's slots in deterministic due-time order."""
        return tuple(
            sorted(
                (item for key, item in self._slots.items() if key[0] == epoch_id),
                key=lambda item: (item.due_at, item.schedule_kind),
            )
        )

    def attempts(
        self,
        epoch_id: UUID,
        slot_key: str,
    ) -> tuple[AttemptOutcome, ...]:
        """Return every distinct retained outcome for a slot key."""
        return tuple(self._attempts.get((epoch_id, slot_key), ()))

    def accepted(
        self,
        epoch_id: UUID,
        schedule_kind: ScheduleKind,
        slot_key: str,
    ) -> bool:
        """Return exact CAS acceptance state."""
        return (epoch_id, schedule_kind, slot_key) in self._accepted

    def force_unaccept(self, epoch_id: UUID, slot: CadenceSlot) -> None:
        """Test-only current-state mutation used to prove refresh semantics."""
        self._accepted.discard((epoch_id, slot.schedule_kind, slot.slot_key))


__all__ = ("CadenceStore", "InMemoryCadenceStore")
