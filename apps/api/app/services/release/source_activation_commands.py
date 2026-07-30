"""Transactional phase commands for the Manifold activation gate."""

# ruff: noqa: D101, D103, PLR0913, TC001, TC003

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid5

from scripts.activation_evidence_models import PublicActivationAttestation

from . import source_activation as activation_db
from .source_activation_domain import CommitInput, ReserveInput
from .source_activation_plans import (
    plan_commit,
    plan_reserve,
    write_commit,
    write_reserve,
)
from .source_activation_receipts import (
    ActivateRequest,
    ActivationOutput,
    ChainReceipt,
    FreeTierResult,
    canonical_bytes,
    hash_document,
    load_receipt,
)
from .source_activation_state import LockedActivationState

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

    from .source_activation_domain import CommitPlan, ReservePlan
NAMESPACE = UUID("cd932162-ffd4-5bb4-b027-cbdb38a789d3")


@dataclass(frozen=True, slots=True)
class PhaseContext:
    connection: AsyncConnection
    request: ActivateRequest
    locked: LockedActivationState
    db_now: datetime


def required[T](value: T | None) -> T:
    if value is None:
        error_code = "phase_argument_missing"
        raise ValueError(error_code)
    return value


def chain(path: Path, command: str, state: str) -> ChainReceipt:
    receipt = load_receipt(path, ChainReceipt)
    if (
        receipt.command != command
        or receipt.state_after != state
        or not receipt.accepted
    ):
        error_code = "receipt_semantics_mismatch"
        raise ValueError(error_code)
    return receipt


def common_evidence(
    context: PhaseContext,
) -> tuple[PublicActivationAttestation, FreeTierResult]:
    request = context.request
    attestation = load_receipt(
        required(request.attestation),
        PublicActivationAttestation,
    )
    free_tier = load_receipt(required(request.free_tier_result), FreeTierResult)
    state = context.locked.state
    bindings_match = (
        request.activation_nonce
        == state.activation_nonce
        == attestation.activation_nonce
        and request.expected_sha == attestation.reviewed_sha == free_tier.reviewed_sha
        and attestation.attestation_generation == state.attestation_generation
        and sha256(canonical_bytes(attestation)).hexdigest() == state.attestation_sha256
        and attestation.free_tier_evidence_sha256 == free_tier.receipt_sha256
    )
    if not bindings_match:
        error_code = "activation_binding_mismatch"
        raise ValueError(error_code)
    return attestation, free_tier


def transition_identity(
    context: PhaseContext,
    receipt_sha256: str,
) -> activation_db.TransitionIdentity:
    locked = context.locked
    return activation_db.TransitionIdentity(
        activation_nonce=context.request.activation_nonce,
        attestation_id=locked.attestation_id,
        binding_intent_id=locked.binding_intent_id,
        predecessor_transition_id=locked.transition_id,
        receipt_sha256=receipt_sha256,
        transition_id=uuid5(NAMESPACE, f"transition:{receipt_sha256}"),
    )


def activation_output(
    context: PhaseContext,
    *,
    command: Literal[
        "activation-reserve",
        "activation-commit",
        "activation-reprepare",
        "activation-restore",
    ],
    accepted: bool,
    state_after: str,
    generation: int,
    attestation_sha256: str,
    predecessor_sha256: str,
    cadence_anchor_at: str | None = None,
    reason: str | None = None,
) -> ActivationOutput:
    body = {
        "schema_version": 1,
        "command": command,
        "accepted": accepted,
        "activation_nonce": str(context.request.activation_nonce),
        "reviewed_sha": context.request.expected_sha,
        "db_now": context.db_now.isoformat(),
        "state_before": context.locked.state.state,
        "state_after": state_after,
        "attestation_generation": generation,
        "attestation_sha256": attestation_sha256,
        "predecessor_receipt_sha256": predecessor_sha256,
        "cadence_anchor_at": cadence_anchor_at,
        "reason": reason,
    }
    return ActivationOutput.model_validate(
        {**body, "receipt_sha256": hash_document(body)}
    )


async def reserve(context: PhaseContext) -> ActivationOutput:
    attestation, _ = common_evidence(context)
    handshake = chain(
        required(context.request.binding_handshake_receipt),
        "handshake-github",
        "handshake_passed",
    )
    if handshake.activation_nonce != context.request.activation_nonce:
        error_code = "handshake_nonce_mismatch"
        raise ValueError(error_code)
    plan: ReservePlan = plan_reserve(
        ReserveInput(
            context.db_now,
            context.locked.state,
            handshake.predecessor_receipt_sha256 or handshake.receipt_sha256,
            handshake.receipt_sha256,
        )
    )
    output = activation_output(
        context,
        command="activation-reserve",
        accepted=True,
        state_after=plan.next_state,
        generation=plan.attestation_generation,
        attestation_sha256=plan.attestation_sha256,
        predecessor_sha256=handshake.receipt_sha256,
        cadence_anchor_at=plan.cadence_anchor_at.isoformat(),
    )
    cadence_id = uuid5(NAMESPACE, f"cadence:{output.receipt_sha256}")
    await write_reserve(
        context.connection,
        activation_db.ReserveWrite(
            transition_identity(context, output.receipt_sha256),
            context.db_now,
            cadence_id,
            plan.cadence_anchor_at,
            plan.cadence_anchor_at + timedelta(days=31),
            plan.cadence_anchor_at + timedelta(days=30),
        ),
    )
    del attestation
    return output


async def commit(context: PhaseContext) -> ActivationOutput:
    attestation, free_tier = common_evidence(context)
    handshake = chain(
        required(context.request.binding_handshake_receipt),
        "handshake-github",
        "handshake_passed",
    )
    reserve_receipt = chain(
        required(context.request.activation_reserve_receipt),
        "activation-reserve",
        "anchor_reserved",
    )
    finalized = chain(
        required(context.request.binding_finalize_receipt),
        "finalize-github",
        "github_finalized",
    )
    if reserve_receipt.cadence_anchor_at is None:
        error_code = "cadence_anchor_missing"
        raise ValueError(error_code)
    anchor = datetime.fromisoformat(reserve_receipt.cadence_anchor_at)
    plan: CommitPlan = plan_commit(
        CommitInput(
            context.db_now,
            context.locked.state,
            anchor,
            finalized.predecessor_receipt_sha256 or finalized.receipt_sha256,
            sha256(canonical_bytes(attestation)).hexdigest(),
            handshake.receipt_sha256,
            finalized.receipt_sha256,
            finalized.payload_sha256 or context.locked.binding_payload_sha256,
        )
    )
    output = activation_output(
        context,
        command="activation-commit",
        accepted=plan.accepted,
        state_after=plan.next_state,
        generation=plan.attestation_generation,
        attestation_sha256=plan.attestation_sha256,
        predecessor_sha256=finalized.receipt_sha256,
        cadence_anchor_at=anchor.isoformat(),
        reason=plan.reason,
    )
    cadence_id = context.locked.cadence_id or uuid5(
        NAMESPACE, f"cadence:{reserve_receipt.receipt_sha256}"
    )
    await write_commit(
        context.connection,
        activation_db.CommitWrite(
            transition_identity(context, output.receipt_sha256),
            context.db_now,
            cadence_id,
            uuid5(
                NAMESPACE,
                f"{context.request.activation_nonce}:{context.locked.state.attestation_sha256}:authorization",
            ),
            uuid5(
                NAMESPACE,
                f"{context.request.activation_nonce}:{context.locked.state.attestation_sha256}:budget",
            ),
            attestation.authorization_evidence_sha256,
            free_tier.receipt_sha256,
            plan.effective_at or context.db_now,
            plan.expires_at or anchor + timedelta(days=31),
            plan.accepted,
        ),
    )
    return output
