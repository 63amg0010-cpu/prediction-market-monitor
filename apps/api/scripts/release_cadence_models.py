"""Immutable values for the 30-day two-source cadence contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, override

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

ScheduleKind = Literal["collection", "verifier"]
CadenceStatus = Literal["OPERATIONAL_PENDING_CADENCE", "HOLD", "COMPLETE"]


class AcceptancePhase(StrEnum):
    """The day-zero observation and 30-day acceptance phases."""

    STATUS = "status"
    ACCEPTANCE = "acceptance"


@dataclass(frozen=True, slots=True)
class CadenceError(Exception):
    """Stable fail-closed cadence rejection."""

    code: str

    @override
    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class CadenceEpoch:
    """One immutable window and its frozen source and binding identity."""

    epoch_id: UUID
    anchor_at: datetime
    closes_at: datetime
    epoch_sha256: str
    expected_source_ids: tuple[UUID, UUID]
    binding_sha256: str
    scope_sha256: str
    invalidated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CadenceSlot:
    """One workflow slot, never multiplied by source count."""

    epoch_id: UUID
    schedule_kind: ScheduleKind
    slot_key: str
    due_at: datetime


@dataclass(frozen=True, slots=True)
class SourceSubreceipt:
    """A schema-closed success projection for one expected source."""

    source_id: UUID
    succeeded: bool
    receipt_sha256: str


@dataclass(frozen=True, slots=True)
class CadenceAttempt:
    """One retained scheduled or excluded manual workflow attempt."""

    attempt_id: UUID
    epoch_id: UUID
    schedule_kind: str
    slot_key: str
    mode: str
    started_at: datetime
    completed_at: datetime
    epoch_sha256: str
    binding_sha256: str
    scope_sha256: str
    source_subreceipts: tuple[SourceSubreceipt, ...]


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    """The atomic compare-and-set outcome for one retained attempt."""

    attempt_id: UUID
    accepted: bool
    reason: str
    retry_permitted: bool


@dataclass(frozen=True, slots=True)
class CadenceReport:
    """A fresh durable-state summary for status or final acceptance."""

    phase: AcceptancePhase
    epoch_id: UUID
    expected_source_ids: tuple[UUID, UUID]
    expected_collection_slots: int
    expected_verifier_slots: int
    accepted_collection_slots: int
    accepted_verifier_slots: int
    status: CadenceStatus
    reason: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class RenewalResult:
    """Preserved epoch or terminal invalidation plus a fresh epoch."""

    preserved: bool
    previous_epoch: CadenceEpoch
    epoch: CadenceEpoch
    slots: tuple[CadenceSlot, ...]


__all__ = (
    "AcceptancePhase",
    "AttemptOutcome",
    "CadenceAttempt",
    "CadenceEpoch",
    "CadenceError",
    "CadenceReport",
    "CadenceSlot",
    "RenewalResult",
    "ScheduleKind",
    "SourceSubreceipt",
)
