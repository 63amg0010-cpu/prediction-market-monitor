"""Canonical protected-evidence loading for migration 0011."""

import hashlib
import json
import os
from pathlib import Path
from typing import Never

from pydantic import ValidationError

from scripts.activation_evidence_models import (
    ActivationEvidenceReceipt,
    PublicActivationAttestation,
    canonical_attestation_bytes,
)

MAX_EVIDENCE_BYTES = 65_536
RETAINED_STATE_CONFLICT = "manifold_retained_state_conflict"


class ActivationPreparationError(RuntimeError):
    """Stable redacted refusal for invalid or conflicting activation evidence."""


def reject(code: str) -> Never:
    """Raise only a bounded non-secret activation refusal code."""
    raise ActivationPreparationError(code)


def canonical_bytes(model: PublicActivationAttestation) -> bytes:
    """Serialize the schema-closed attestation into its hash-bound bytes."""
    return canonical_attestation_bytes(model)


def load_evidence(
    expected_scope_version: str,
) -> tuple[PublicActivationAttestation, bytes, str, str]:
    """Load bounded canonical evidence and verify the scoped API receipt binding."""
    try:
        attestation_path = Path(os.environ["MIGRATION_ACTIVATION_ATTESTATION_PATH"])
        receipt_path = Path(os.environ["MIGRATION_ACTIVATION_EVIDENCE_RECEIPT_PATH"])
        attestation_bytes = attestation_path.read_bytes()
        receipt_bytes = receipt_path.read_bytes()
        if (
            len(attestation_bytes) > MAX_EVIDENCE_BYTES
            or len(receipt_bytes) > MAX_EVIDENCE_BYTES
        ):
            reject("activation_evidence_oversize")
        attestation = PublicActivationAttestation.model_validate_json(attestation_bytes)
        receipt = ActivationEvidenceReceipt.model_validate_json(receipt_bytes)
    except (KeyError, OSError, ValidationError):
        reject("activation_evidence_invalid")
    canonical = canonical_bytes(attestation)
    attestation_sha = hashlib.sha256(canonical).hexdigest()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    canonical_receipt = json.dumps(
        receipt.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    if attestation_bytes != canonical or receipt_bytes != canonical_receipt:
        reject("activation_evidence_noncanonical")
    if (
        receipt.attestation_sha256 != attestation_sha
        or receipt.activation_nonce != attestation.activation_nonce
        or receipt.attestation_generation != attestation.attestation_generation
        or receipt.head_sha != attestation.reviewed_sha
        or attestation.source_scope_version != expected_scope_version
    ):
        reject("activation_evidence_binding_mismatch")
    return attestation, canonical, attestation_sha, receipt_sha


__all__ = (
    "RETAINED_STATE_CONFLICT",
    "canonical_bytes",
    "load_evidence",
    "reject",
)
