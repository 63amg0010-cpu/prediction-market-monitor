"""Pure plans and caller-transaction writes for source activation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, NoReturn

from . import source_activation as activation_db
from .source_activation_domain import (
    ActivationHoldError,
    ActivationState,
    ActivationTransition,
    CommitInput,
    CommitPlan,
    ReprepareInput,
    RepreparePlan,
    ReserveInput,
    ReservePlan,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

SHA256_LENGTH: Final = 64


def _hold(code: str) -> NoReturn:
    raise ActivationHoldError(code)


def _require_inert(state: ActivationState) -> None:
    pointers = (
        state.active_authorization_id,
        state.current_budget_id,
        state.current_binding_id,
        state.current_cadence_id,
    )
    if state.source_enabled or any(pointer is not None for pointer in pointers):
        _hold("source_not_inert")


def _require_hashes(*values: str) -> None:
    hexadecimal = frozenset("0123456789abcdef")
    if any(
        len(value) != SHA256_LENGTH or not set(value).issubset(hexadecimal)
        for value in values
    ):
        _hold("receipt_provenance_invalid")


def _preparation_usable(prepared_at: datetime, db_now: datetime) -> bool:
    return prepared_at <= db_now < prepared_at + timedelta(hours=2)


def plan_reserve(value: ReserveInput) -> ReservePlan:
    """Plan a reservation from one caller-owned PostgreSQL transaction time."""
    _require_inert(value.state)
    _require_hashes(
        value.state.attestation_sha256,
        value.predecessor_sha256,
        value.handshake_receipt_sha256,
    )
    normal = value.state.state == "handshake_passed"
    same_generation_retry = (
        value.state.state == "failed" and not value.state.binding_write_occurred
    )
    if not normal and not same_generation_retry:
        _hold("reserve_state_invalid")
    if not _preparation_usable(value.state.prepared_at, value.db_now):
        _hold("preparation_not_usable")
    anchor = value.db_now.replace(minute=0, second=0, microsecond=0) + timedelta(
        hours=3
    )
    return ReservePlan(
        db_now=value.db_now,
        activation_nonce=value.state.activation_nonce,
        attestation_generation=value.state.attestation_generation,
        attestation_sha256=value.state.attestation_sha256,
        predecessor_sha256=value.predecessor_sha256,
        handshake_receipt_sha256=value.handshake_receipt_sha256,
        cadence_anchor_at=anchor,
        next_state="anchor_reserved",
    )


def plan_commit(value: CommitInput) -> CommitPlan:
    """Plan an atomic activation or a durable failed reservation."""
    _require_inert(value.state)
    _require_hashes(
        value.state.attestation_sha256,
        value.predecessor_sha256,
        value.attestation_receipt_sha256,
        value.handshake_receipt_sha256,
        value.finalize_receipt_sha256,
        value.journal_payload_sha256,
    )
    if value.state.state != "github_finalized":
        _hold("commit_state_invalid")
    preparation_usable = _preparation_usable(value.state.prepared_at, value.db_now)
    before_cutoff = value.db_now < value.cadence_anchor_at - timedelta(hours=1)
    if not preparation_usable:
        return _failed_commit(value, "preparation_not_usable")
    if not before_cutoff:
        return _failed_commit(value, "activation_cutoff_reached")
    return CommitPlan(
        accepted=True,
        db_now=value.db_now,
        activation_nonce=value.state.activation_nonce,
        attestation_generation=value.state.attestation_generation,
        attestation_sha256=value.state.attestation_sha256,
        cadence_anchor_at=value.cadence_anchor_at,
        effective_at=value.db_now,
        expires_at=value.cadence_anchor_at + timedelta(days=31),
        next_state="active",
        reason=None,
    )


def _failed_commit(value: CommitInput, reason: str) -> CommitPlan:
    return CommitPlan(
        accepted=False,
        db_now=value.db_now,
        activation_nonce=value.state.activation_nonce,
        attestation_generation=value.state.attestation_generation,
        attestation_sha256=value.state.attestation_sha256,
        cadence_anchor_at=value.cadence_anchor_at,
        effective_at=None,
        expires_at=None,
        next_state="failed",
        reason=reason,
    )


def plan_reprepare(value: ReprepareInput) -> RepreparePlan:
    """Plan one immutable next-generation preparation."""
    _require_inert(value.state)
    _require_hashes(
        value.state.attestation_sha256,
        value.previous_attestation_sha256,
        value.failed_reservation_sha256,
        value.fresh_evidence_sha256,
    )
    if value.state.state not in {"failed", "restore_writing"}:
        _hold("reprepare_state_invalid")
    if value.previous_attestation_sha256 != value.state.attestation_sha256:
        _hold("previous_attestation_mismatch")
    if value.requested_generation != value.state.attestation_generation + 1:
        _hold("attestation_generation_not_next")
    if value.fresh_evidence_activation_nonce != value.state.activation_nonce:
        _hold("activation_nonce_mismatch")
    if value.fresh_evidence_sha256 == value.state.attestation_sha256:
        _hold("activation_evidence_reused")
    if not _preparation_usable(value.fresh_evidence_prepared_at, value.db_now):
        _hold("activation_evidence_not_fresh")
    if value.state.binding_write_occurred and not value.state.restore_verified:
        _hold("binding_restore_required")
    return RepreparePlan(
        db_now=value.db_now,
        activation_nonce=value.state.activation_nonce,
        previous_generation=value.state.attestation_generation,
        attestation_generation=value.requested_generation,
        previous_attestation_sha256=value.previous_attestation_sha256,
        failed_reservation_sha256=value.failed_reservation_sha256,
        attestation_sha256=value.fresh_evidence_sha256,
        next_state="prepared",
    )


def plan_restore(
    *,
    current_state: ActivationTransition,
) -> tuple[Literal["deactivated"], Literal["restore_writing"]]:
    """Keep terminal restored ownership outside binding restore helpers."""
    if current_state != "failed":
        _hold("restore_state_invalid")
    return ("deactivated", "restore_writing")


async def write_reserve(
    connection: AsyncConnection,
    value: activation_db.ReserveWrite,
) -> None:
    """Persist one planned reservation in its caller-owned transaction."""
    await activation_db.persist_reserve(connection, value)


async def write_commit(
    connection: AsyncConnection,
    value: activation_db.CommitWrite,
) -> None:
    """Persist one planned commit in its caller-owned transaction."""
    await activation_db.persist_commit(connection, value)


async def write_reprepare(
    connection: AsyncConnection,
    value: activation_db.ReprepareWrite,
) -> None:
    """Persist one planned reprepare in its caller-owned transaction."""
    await activation_db.persist_reprepare(connection, value)


async def write_restore(
    connection: AsyncConnection,
    value: activation_db.RestoreWrite,
) -> None:
    """Persist one planned restore in its caller-owned transaction."""
    await activation_db.persist_restore(connection, value)
