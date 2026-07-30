"""Immutable reprepare and nonterminal restore activation phases."""


from __future__ import annotations

from hashlib import sha256
from uuid import uuid5

from scripts.activation_evidence_models import (
    ActivationEvidenceReceipt,
    PublicActivationAttestation,
)

from . import source_activation as activation_db
from .source_activation_commands import (
    NAMESPACE,
    PhaseContext,
    activation_output,
    chain,
    required,
    transition_identity,
)
from .source_activation_domain import ReprepareInput
from .source_activation_plans import (
    plan_reprepare,
    plan_restore,
    write_reprepare,
    write_restore,
)
from .source_activation_receipts import (
    ActivationOutput,
    canonical_bytes,
    load_receipt,
)


async def reprepare(context: PhaseContext) -> ActivationOutput:
    """Append one fresh generation without executing migration 0011 again."""
    request = context.request
    failed = chain(
        required(request.failed_reservation_receipt),
        "activation-commit",
        "failed",
    )
    previous = load_receipt(
        required(request.previous_attestation_receipt),
        PublicActivationAttestation,
    )
    evidence_path = required(request.activation_evidence_receipt)
    evidence = load_receipt(evidence_path, ActivationEvidenceReceipt)
    generation = required(request.attestation_generation)
    attestation_path = (
        evidence_path.parent / f"activation-attestation-generation-{generation}.json"
    )
    fresh = load_receipt(attestation_path, PublicActivationAttestation)
    previous_sha = sha256(canonical_bytes(previous)).hexdigest()
    fresh_sha = sha256(canonical_bytes(fresh)).hexdigest()
    bindings_match = (
        failed.activation_nonce == request.activation_nonce
        and failed.attestation_sha256 == previous_sha
        and previous_sha == context.locked.state.attestation_sha256
        and evidence.activation_nonce
        == fresh.activation_nonce
        == request.activation_nonce
        and evidence.attestation_generation
        == fresh.attestation_generation
        == generation
        and evidence.attestation_sha256 == fresh_sha
        and evidence.head_sha == fresh.reviewed_sha == request.expected_sha
    )
    if not bindings_match:
        error_code = "reprepare_receipt_binding_mismatch"
        raise ValueError(error_code)
    plan = plan_reprepare(
        ReprepareInput(
            context.db_now,
            context.locked.state,
            generation,
            previous_sha,
            failed.receipt_sha256,
            fresh_sha,
            fresh.captured_at,
            fresh.activation_nonce,
        )
    )
    evidence_sha = sha256(canonical_bytes(evidence)).hexdigest()
    output = activation_output(
        context,
        command="activation-reprepare",
        accepted=True,
        state_after=plan.next_state,
        generation=plan.attestation_generation,
        attestation_sha256=plan.attestation_sha256,
        predecessor_sha256=evidence_sha,
    )
    new_attestation_id = uuid5(
        NAMESPACE,
        f"attestation:{request.activation_nonce}:{generation}:{fresh_sha}",
    )
    payload_sha = sha256(
        f"{context.locked.binding_payload_sha256}:{fresh_sha}".encode()
    ).hexdigest()
    await write_reprepare(
        context.connection,
        activation_db.ReprepareWrite(
            transition_identity(context, output.receipt_sha256),
            context.db_now,
            new_attestation_id,
            generation,
            fresh_sha,
            canonical_bytes(fresh),
            request.expected_sha,
            fresh.authorization_evidence_sha256,
            fresh.free_tier_evidence_sha256,
            fresh.provenance_sha256,
            fresh.evidence_database_time,
            fresh.captured_at,
            payload_sha,
        ),
    )
    return output


async def restore(context: PhaseContext) -> ActivationOutput:
    """Stop at restore_writing so only a terminal finalizer can restore."""
    request = context.request
    failed = chain(
        required(request.failed_reservation_receipt),
        "activation-commit",
        "failed",
    )
    _ = chain(
        required(request.binding_restore_receipt),
        "restore-github",
        "restore_writing",
    )
    verified = chain(
        required(request.restore_verification_receipt),
        "binding-restore-verify",
        "restore_writing",
    )
    if failed.activation_nonce != request.activation_nonce:
        error_code = "restore_nonce_mismatch"
        raise ValueError(error_code)
    deactivated, restore_writing = plan_restore(
        current_state=context.locked.state.state
    )
    output = activation_output(
        context,
        command="activation-restore",
        accepted=True,
        state_after=restore_writing,
        generation=context.locked.state.attestation_generation,
        attestation_sha256=context.locked.state.attestation_sha256,
        predecessor_sha256=verified.receipt_sha256,
    )
    await write_restore(
        context.connection,
        activation_db.RestoreWrite(
            transition_identity(context, output.receipt_sha256),
            context.db_now,
            uuid5(NAMESPACE, f"deactivated:{output.receipt_sha256}"),
            sha256(f"{deactivated}:{verified.receipt_sha256}".encode()).hexdigest(),
        ),
    )
    return output


__all__ = ("reprepare", "restore")
