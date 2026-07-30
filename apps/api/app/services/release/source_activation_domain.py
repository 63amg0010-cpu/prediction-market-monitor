"""Typed values for source activation planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, override

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

ActivationTransition = Literal[
    "prepared",
    "binding_writing",
    "binding_committed",
    "handshake_passed",
    "anchor_reserved",
    "github_finalized",
    "active",
    "deactivated",
    "restore_writing",
    "restored",
    "failed",
]


@dataclass(frozen=True, slots=True)
class ActivationHoldError(Exception):
    """Stable fail-closed release-gate rejection."""

    code: str

    @override
    def __str__(self) -> str:
        """Return the stable redacted rejection code."""
        return self.code


@dataclass(frozen=True, slots=True)
class ActivationState:
    """Latest valid transition and explicit current source pointers."""

    activation_nonce: UUID
    attestation_generation: int
    attestation_sha256: str
    prepared_at: datetime
    state: ActivationTransition
    source_enabled: bool
    active_authorization_id: UUID | None
    current_budget_id: UUID | None
    current_binding_id: UUID | None
    current_cadence_id: UUID | None
    binding_write_occurred: bool
    restore_verified: bool


@dataclass(frozen=True, slots=True)
class ReserveInput:
    """Inputs read under the reservation transaction."""

    db_now: datetime
    state: ActivationState
    predecessor_sha256: str
    handshake_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class ReservePlan:
    """Immutable values written by a successful reservation."""

    db_now: datetime
    activation_nonce: UUID
    attestation_generation: int
    attestation_sha256: str
    predecessor_sha256: str
    handshake_receipt_sha256: str
    cadence_anchor_at: datetime
    next_state: Literal["anchor_reserved"]


@dataclass(frozen=True, slots=True)
class CommitInput:
    """Inputs locked and verified by the activation transaction."""

    db_now: datetime
    state: ActivationState
    cadence_anchor_at: datetime
    predecessor_sha256: str
    attestation_receipt_sha256: str
    handshake_receipt_sha256: str
    finalize_receipt_sha256: str
    journal_payload_sha256: str


@dataclass(frozen=True, slots=True)
class CommitPlan:
    """Atomic activation result or durable failed reservation."""

    accepted: bool
    db_now: datetime
    activation_nonce: UUID
    attestation_generation: int
    attestation_sha256: str
    cadence_anchor_at: datetime
    effective_at: datetime | None
    expires_at: datetime | None
    next_state: Literal["active", "failed"]
    reason: str | None


@dataclass(frozen=True, slots=True)
class ReprepareInput:
    """Inputs consumed by next-generation preparation."""

    db_now: datetime
    state: ActivationState
    requested_generation: int
    previous_attestation_sha256: str
    failed_reservation_sha256: str
    fresh_evidence_sha256: str
    fresh_evidence_prepared_at: datetime
    fresh_evidence_activation_nonce: UUID


@dataclass(frozen=True, slots=True)
class RepreparePlan:
    """Immutable next-generation preparation values."""

    db_now: datetime
    activation_nonce: UUID
    previous_generation: int
    attestation_generation: int
    previous_attestation_sha256: str
    failed_reservation_sha256: str
    attestation_sha256: str
    next_state: Literal["prepared"]
