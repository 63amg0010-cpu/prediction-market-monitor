"""Core validation for immutable reviewed migration dispatches."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import re
from hashlib import sha256
from typing import Final, Literal, Never, cast
from uuid import UUID

from app.domain.types import JsonValue
from pydantic import BaseModel, TypeAdapter, ValidationError

from scripts.migration_dispatch_models import (
    DispatchRequest,
    FailedAttemptReceipt,
    NoSpendReceipt,
    ReviewRoot,
    ValidatedDispatch,
)

MAX_DECODED_BODY_BYTES: Final = 8192
SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_JSON_ADAPTER: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)


class DispatchValidationError(RuntimeError):
    """Stable fail-closed error whose message never contains transported bodies."""


def canonical_body(value: JsonValue) -> bytes:
    """Encode the schema-closed JSON subset deterministically."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def reject(code: str) -> Never:
    """Raise a stable dispatch validation error for the given code."""
    raise DispatchValidationError(code)


def require_sha(value: str, *, sha256_value: bool = False) -> None:
    """Require a lowercase SHA-1 or SHA-256 hexadecimal value."""
    pattern = SHA256_PATTERN if sha256_value else SHA_PATTERN
    if pattern.fullmatch(value) is None:
        reject("invalid_hash")


def parse_body[ModelT: BaseModel](
    encoded: str, declared_hash: str, model: type[ModelT]
) -> ModelT:
    """Decode and validate a canonical, hash-bound dispatch body."""
    require_sha(declared_hash, sha256_value=True)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        error_code = "invalid_base64_body"
        raise DispatchValidationError(error_code) from error
    if len(decoded) > MAX_DECODED_BODY_BYTES:
        reject("body_too_large")
    try:
        parsed = _JSON_ADAPTER.validate_json(decoded)
        validated = model.model_validate(parsed)
    except ValidationError as error:
        error_code = "invalid_schema_closed_body"
        raise DispatchValidationError(error_code) from error
    canonical = canonical_body(validated.model_dump(mode="json"))
    if not hmac.compare_digest(decoded, canonical):
        reject("noncanonical_body")
    if not hmac.compare_digest(sha256(decoded).hexdigest(), declared_hash):
        reject("body_hash_mismatch")
    return validated


def _validate_common(
    request: DispatchRequest, actual_event_sha: str | None
) -> tuple[Literal[1, 2], str]:
    require_sha(request.expected_commit_sha)
    require_sha(request.expected_plan_sha256, sha256_value=True)
    if request.attempt not in {1, 2}:
        reject("invalid_attempt")
    if request.activation_nonce == request.dispatch_nonce:
        reject("nonce_reuse")
    if actual_event_sha is not None and not hmac.compare_digest(
        request.expected_commit_sha, actual_event_sha
    ):
        reject("event_sha_mismatch")
    attempt: Literal[1, 2] = 1 if request.attempt == 1 else 2
    title = (
        f"migrate-{request.operation}-{request.revision}-"
        f"{request.dispatch_nonce}-attempt-{attempt}"
    )
    return attempt, title


def _validate_bootstrap(request: DispatchRequest, attempt: Literal[1, 2]) -> None:
    if request.reservation_sha256:
        reject("bootstrap_reservation_forbidden")
    root = parse_body(request.review_root_b64, request.review_root_sha256, ReviewRoot)
    no_spend = parse_body(
        request.no_spend_receipt_b64,
        request.no_spend_receipt_sha256,
        NoSpendReceipt,
    )
    bindings_match = (
        root.reviewed_sha == request.expected_commit_sha
        and root.approved_plan_sha256 == request.expected_plan_sha256
        and root.activation_nonce == request.activation_nonce
        and no_spend.reviewed_sha == root.reviewed_sha
        and no_spend.approved_plan_sha256 == root.approved_plan_sha256
        and no_spend.activation_nonce == root.activation_nonce
        and no_spend.predecessor_receipt_sha256 == request.review_root_sha256
    )
    for value in (
        root.approval_round_id,
        *root.approval_launch_sha256s,
        *root.protected_identity_hashes.model_dump().values(),
    ):
        require_sha(str(value), sha256_value=True)
    if not bindings_match:
        reject("bootstrap_binding_mismatch")
    if attempt == 1:
        if (
            request.attempt1_failed_receipt_sha256
            or request.attempt1_failed_receipt_b64
        ):
            reject("attempt_one_proof_must_be_empty")
        return
    proof = parse_body(
        request.attempt1_failed_receipt_b64,
        request.attempt1_failed_receipt_sha256,
        FailedAttemptReceipt,
    )
    proof_matches = (
        proof.reviewed_sha == request.expected_commit_sha
        and proof.approved_plan_sha256 == request.expected_plan_sha256
        and proof.activation_nonce == request.activation_nonce
        and proof.dispatch_nonce != request.dispatch_nonce
        and proof.review_root_sha256 == request.review_root_sha256
        and proof.no_spend_receipt_sha256 == request.no_spend_receipt_sha256
        and proof.run_id > 0
    )
    require_sha(proof.artifact_sha256, sha256_value=True)
    if not proof_matches:
        reject("attempt_two_proof_mismatch")


def _validate_post_ledger(
    request: DispatchRequest,
    claimed_reservation_sha256: str | None,
    *,
    defer_reservation_claim: bool,
) -> None:
    if any(
        (
            request.review_root_sha256,
            request.review_root_b64,
            request.no_spend_receipt_sha256,
            request.no_spend_receipt_b64,
            request.attempt1_failed_receipt_sha256,
            request.attempt1_failed_receipt_b64,
        )
    ):
        reject("post_ledger_bootstrap_body_forbidden")
    require_sha(request.reservation_sha256, sha256_value=True)
    if not defer_reservation_claim and (
        claimed_reservation_sha256 is None
        or not hmac.compare_digest(
            request.reservation_sha256, claimed_reservation_sha256
        )
    ):
        reject("reservation_claim_mismatch")
    if request.revision == "20260727_0011":
        if (
            not request.attestation_run_id.isdecimal()
            or int(request.attestation_run_id) < 1
            or request.attestation_generation < 1
        ):
            reject("attestation_run_required")
        try:
            attestation_dispatch_nonce = UUID(request.attestation_dispatch_nonce)
        except ValueError as error:
            error_code = "attestation_dispatch_nonce_invalid"
            raise DispatchValidationError(error_code) from error
        if attestation_dispatch_nonce in {
            request.activation_nonce,
            request.dispatch_nonce,
        }:
            reject("attestation_dispatch_nonce_reuse")
        require_sha(request.attestation_sha256, sha256_value=True)
        return
    if (
        request.attestation_run_id
        or request.attestation_generation
        or request.attestation_dispatch_nonce
        or request.attestation_sha256
    ):
        reject("attestation_forbidden")


def validate_dispatch(
    request: DispatchRequest,
    *,
    actual_event_sha: str | None = None,
    claimed_reservation_sha256: str | None = None,
    defer_reservation_claim: bool = False,
) -> ValidatedDispatch:
    """Return the only inert Alembic argv permitted by a reviewed dispatch."""
    attempt, display_title = _validate_common(request, actual_event_sha)
    tuple_key = (request.operation, request.revision, request.confirm)
    allowed = {
        ("upgrade", "20260727_0010", "migrate-production"),
        ("upgrade", "20260803_0010a", "repair-release-foundation"),
        ("upgrade", "20260803_0010b", "rebind-release-root"),
        ("upgrade", "20260727_0011", "migrate-production"),
        ("downgrade", "20260803_0010b", "rollback-manifold"),
    }
    if tuple_key not in allowed:
        reject("operation_tuple_rejected")
    operation: Literal["upgrade", "downgrade"] = (
        "upgrade" if request.operation == "upgrade" else "downgrade"
    )
    revision: Literal[
        "20260727_0010",
        "20260803_0010a",
        "20260803_0010b",
        "20260727_0011",
    ] = cast(
        "Literal['20260727_0010', '20260803_0010a', '20260803_0010b', '20260727_0011']",
        request.revision,
    )
    if tuple_key in {
        ("upgrade", "20260727_0010", "migrate-production"),
        ("upgrade", "20260803_0010a", "repair-release-foundation"),
        ("upgrade", "20260803_0010b", "rebind-release-root"),
    }:
        if (
            request.attestation_run_id
            or request.attestation_generation
            or request.attestation_dispatch_nonce
            or request.attestation_sha256
        ):
            reject("bootstrap_attestation_forbidden")
        if request.revision in {"20260803_0010a", "20260803_0010b"} and attempt != 1:
            reject("release_correction_attempt_invalid")
        _validate_bootstrap(request, attempt)
    else:
        _validate_post_ledger(
            request,
            claimed_reservation_sha256,
            defer_reservation_claim=defer_reservation_claim,
        )
    command = "upgrade" if operation == "upgrade" else "downgrade"
    return ValidatedDispatch(
        operation=operation,
        revision=revision,
        attempt=attempt,
        display_title=display_title,
        alembic_argv=(
            "alembic",
            "-c",
            "apps/api/alembic.ini",
            command,
            revision,
        ),
    )
