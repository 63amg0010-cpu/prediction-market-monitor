"""Fail-closed validation for immutable reviewed migration workflow inputs."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import sys
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal, Never, cast
from urllib.parse import unquote, urlsplit
from uuid import UUID

from app.domain.types import JsonValue
from pydantic import BaseModel, TypeAdapter, ValidationError

from scripts.migration_dispatch_models import (
    DispatchRequest,
    FailedAttemptReceipt,
    NoSpendReceipt,
    ReviewRoot,
    RunCandidate,
    ValidatedDispatch,
)

MAX_DECODED_BODY_BYTES: Final = 8192
SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
CURRENT_PATTERN: Final = re.compile(
    r"^((?:2026072[67]_[0-9]{4})|20260803_0010[abcd])(?: \(head\))?$"
)
EXPECTED_HEAD: Final = "20260727_0011"
WORKFLOW_PATH: Final = ".github/workflows/migrate.yml"
ARGUMENT_COUNT: Final = 2
POSTGRES_SESSION_PORT: Final = 5432
_JSON_ADAPTER: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
_RUNS_ADAPTER: Final[TypeAdapter[tuple[RunCandidate, ...]]] = TypeAdapter(
    tuple[RunCandidate, ...]
)
type MigrationRevision = Literal[
    "20260727_0010",
    "20260803_0010a",
    "20260803_0010b",
    "20260803_0010c",
    "20260803_0010d",
    "20260727_0011",
]


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


def _reject(code: str) -> Never:
    raise DispatchValidationError(code)


def _require_sha(value: str, *, sha256_value: bool = False) -> None:
    pattern = SHA256_PATTERN if sha256_value else SHA_PATTERN
    if pattern.fullmatch(value) is None:
        _reject("invalid_hash")


def _parse_body[ModelT: BaseModel](
    encoded: str, declared_hash: str, model: type[ModelT]
) -> ModelT:
    _require_sha(declared_hash, sha256_value=True)
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        error_code = "invalid_base64_body"
        raise DispatchValidationError(error_code) from error
    if len(decoded) > MAX_DECODED_BODY_BYTES:
        _reject("body_too_large")
    try:
        parsed = _JSON_ADAPTER.validate_json(decoded)
        validated = model.model_validate(parsed)
    except ValidationError as error:
        error_code = "invalid_schema_closed_body"
        raise DispatchValidationError(error_code) from error
    canonical = canonical_body(validated.model_dump(mode="json"))
    if not hmac.compare_digest(decoded, canonical):
        _reject("noncanonical_body")
    if not hmac.compare_digest(sha256(decoded).hexdigest(), declared_hash):
        _reject("body_hash_mismatch")
    return validated


def _validate_common(
    request: DispatchRequest, actual_event_sha: str | None
) -> tuple[Literal[1, 2], str]:
    _require_sha(request.expected_commit_sha)
    _require_sha(request.expected_plan_sha256, sha256_value=True)
    if request.attempt not in {1, 2}:
        _reject("invalid_attempt")
    if request.activation_nonce == request.dispatch_nonce:
        _reject("nonce_reuse")
    if actual_event_sha is not None and not hmac.compare_digest(
        request.expected_commit_sha, actual_event_sha
    ):
        _reject("event_sha_mismatch")
    attempt: Literal[1, 2] = 1 if request.attempt == 1 else 2
    title = (
        f"migrate-{request.operation}-{request.revision}-"
        f"{request.dispatch_nonce}-attempt-{attempt}"
    )
    return attempt, title


def _validate_bootstrap(request: DispatchRequest, attempt: Literal[1, 2]) -> None:
    if request.reservation_sha256:
        _reject("bootstrap_reservation_forbidden")
    root = _parse_body(request.review_root_b64, request.review_root_sha256, ReviewRoot)
    no_spend = _parse_body(
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
        _require_sha(str(value), sha256_value=True)
    if not bindings_match:
        _reject("bootstrap_binding_mismatch")
    if attempt == 1:
        if (
            request.attempt1_failed_receipt_sha256
            or request.attempt1_failed_receipt_b64
        ):
            _reject("attempt_one_proof_must_be_empty")
        return
    proof = _parse_body(
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
    _require_sha(proof.artifact_sha256, sha256_value=True)
    if not proof_matches:
        _reject("attempt_two_proof_mismatch")


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
        _reject("post_ledger_bootstrap_body_forbidden")
    _require_sha(request.reservation_sha256, sha256_value=True)
    if not defer_reservation_claim and (
        claimed_reservation_sha256 is None
        or not hmac.compare_digest(
            request.reservation_sha256, claimed_reservation_sha256
        )
    ):
        _reject("reservation_claim_mismatch")
    if request.revision == "20260727_0011":
        if (
            not request.attestation_run_id.isdecimal()
            or int(request.attestation_run_id) < 1
            or request.attestation_generation < 1
        ):
            _reject("attestation_run_required")
        try:
            attestation_dispatch_nonce = UUID(request.attestation_dispatch_nonce)
        except ValueError as error:
            error_code = "attestation_dispatch_nonce_invalid"
            raise DispatchValidationError(error_code) from error
        if attestation_dispatch_nonce in {
            request.activation_nonce,
            request.dispatch_nonce,
        }:
            _reject("attestation_dispatch_nonce_reuse")
        _require_sha(request.attestation_sha256, sha256_value=True)
        return
    if (
        request.attestation_run_id
        or request.attestation_generation
        or request.attestation_dispatch_nonce
        or request.attestation_sha256
    ):
        _reject("attestation_forbidden")


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
        ("upgrade", "20260803_0010c", "rebind-release-root"),
        ("upgrade", "20260803_0010d", "rebind-release-root"),
        ("upgrade", "20260727_0011", "migrate-production"),
        ("downgrade", "20260803_0010d", "rollback-manifold"),
    }
    if tuple_key not in allowed:
        _reject("operation_tuple_rejected")
    operation: Literal["upgrade", "downgrade"] = (
        "upgrade" if request.operation == "upgrade" else "downgrade"
    )
    revision = cast("MigrationRevision", request.revision)
    if tuple_key in {
        ("upgrade", "20260727_0010", "migrate-production"),
        ("upgrade", "20260803_0010a", "repair-release-foundation"),
        ("upgrade", "20260803_0010b", "rebind-release-root"),
        ("upgrade", "20260803_0010c", "rebind-release-root"),
        ("upgrade", "20260803_0010d", "rebind-release-root"),
    }:
        if (
            request.attestation_run_id
            or request.attestation_generation
            or request.attestation_dispatch_nonce
            or request.attestation_sha256
        ):
            _reject("bootstrap_attestation_forbidden")
        if request.revision in {
            "20260803_0010a",
            "20260803_0010b",
            "20260803_0010c",
            "20260803_0010d",
        } and attempt != 1:
            _reject("release_correction_attempt_invalid")
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


def validate_heads(output: str) -> None:
    """Require the reviewed checkout to have one exact Alembic head."""
    heads = tuple(
        line.strip().split()[0] for line in output.splitlines() if line.strip()
    )
    if heads != (EXPECTED_HEAD,):
        _reject("unexpected_alembic_heads")


def validate_current(output: str, request: DispatchRequest) -> None:
    """Require the safe starting revision for the selected operation."""
    lines = tuple(line.strip() for line in output.splitlines() if line.strip())
    match = CURRENT_PATTERN.fullmatch(lines[0]) if len(lines) == 1 else None
    if match is None:
        _reject("unexpected_current_revision")
    expected = (
        "20260726_0009"
        if request.revision == "20260727_0010" and request.operation == "upgrade"
        else "20260727_0010"
        if request.revision == "20260803_0010a" and request.operation == "upgrade"
        else "20260803_0010a"
        if request.revision == "20260803_0010b" and request.operation == "upgrade"
        else "20260803_0010b"
        if request.revision == "20260803_0010c" and request.operation == "upgrade"
        else "20260803_0010c"
        if request.revision == "20260803_0010d" and request.operation == "upgrade"
        else "20260803_0010d"
        if request.revision == "20260727_0011"
        else "20260727_0011"
    )
    if match.group(1) != expected:
        _reject("unsafe_current_revision")


def validate_result(output: str, request: DispatchRequest) -> None:
    """Require the database to finish at the exact selected revision."""
    lines = tuple(line.strip() for line in output.splitlines() if line.strip())
    match = CURRENT_PATTERN.fullmatch(lines[0]) if len(lines) == 1 else None
    if match is None or match.group(1) != request.revision:
        _reject("unexpected_result_revision")


def _database_identity(
    url: str,
    schemes: frozenset[str],
) -> tuple[str, int, str, str, str]:
    try:
        parsed = urlsplit(url)
        port = parsed.port or POSTGRES_SESSION_PORT
    except ValueError:
        _reject("database_url_invalid")
    database = unquote(parsed.path.removeprefix("/"))
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    hostname = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in schemes
        or not hostname
        or not database
        or not username
        or not password
        or port != POSTGRES_SESSION_PORT
        or parsed.fragment
    ):
        _reject("database_url_not_direct_or_session_5432")
    return hostname, port, database, username, password


def validate_database_urls(migration: str, dump: str, restore: str) -> None:
    """Require one direct/session-mode 5432 database across async and libpq URLs."""
    migration_identity = _database_identity(
        migration, frozenset({"postgresql+asyncpg"})
    )
    native_schemes = frozenset({"postgres", "postgresql"})
    dump_identity = _database_identity(dump, native_schemes)
    restore_identity = _database_identity(restore, native_schemes)
    if not hmac.compare_digest(
        repr(migration_identity).encode(), repr(dump_identity).encode()
    ) or not hmac.compare_digest(
        repr(migration_identity).encode(), repr(restore_identity).encode()
    ):
        _reject("database_url_identity_mismatch")


def select_unique_run(
    candidates: tuple[RunCandidate, ...], request: DispatchRequest
) -> RunCandidate:
    """Select one exact workflow-dispatch run without stdout inference."""
    expected_title = (
        f"migrate-{request.operation}-{request.revision}-"
        f"{request.dispatch_nonce}-attempt-{request.attempt}"
    )
    matches = tuple(
        candidate
        for candidate in candidates
        if candidate.workflow_path == WORKFLOW_PATH
        and candidate.display_title == expected_title
        and candidate.head_sha == request.expected_commit_sha
        and candidate.event == "workflow_dispatch"
        and candidate.attempt == request.attempt
        and candidate.dispatch_nonce == request.dispatch_nonce
    )
    if len(matches) != 1:
        _reject("run_correlation_not_unique")
    return matches[0]


def _request_from_environment() -> DispatchRequest:
    fields = DispatchRequest.model_fields
    return DispatchRequest.model_validate(
        {name: os.environ.get(name.upper(), "") for name in fields}
    )


def main() -> int:
    """Validate workflow inputs without printing bodies, credentials, or identifiers."""
    try:
        if len(sys.argv) != ARGUMENT_COUNT:
            _reject("usage")
        command = sys.argv[1]
        if command == "validate-database-urls":
            validate_database_urls(
                os.environ.get("MIGRATION_DATABASE_URL", ""),
                os.environ.get("PG_DUMP_DATABASE_URL", ""),
                os.environ.get("PG_RESTORE_DATABASE_URL", ""),
            )
            _ = sys.stdout.write("migration_dispatch_validated\n")
            return 0
        request = _request_from_environment()
        if command == "validate-dispatch":
            _ = validate_dispatch(
                request,
                actual_event_sha=os.environ.get("GITHUB_EVENT_SHA"),
                claimed_reservation_sha256=os.environ.get("CLAIMED_RESERVATION_SHA256"),
                defer_reservation_claim=(
                    os.environ.get("DEFER_RESERVATION_CLAIM") == "true"
                ),
            )
        elif command == "validate-heads":
            validate_heads(Path(os.environ["HEADS_FILE"]).read_text(encoding="utf-8"))
        elif command == "validate-current":
            validate_current(
                Path(os.environ["CURRENT_FILE"]).read_text(encoding="utf-8"),
                request,
            )
        elif command == "validate-result":
            validate_result(
                Path(os.environ["RESULT_FILE"]).read_text(encoding="utf-8"),
                request,
            )
        elif command == "select-run":
            candidates = _RUNS_ADAPTER.validate_json(
                Path(os.environ["RUNS_FILE"]).read_bytes()
            )
            _ = select_unique_run(candidates, request)
        else:
            _reject("usage")
    except (DispatchValidationError, KeyError, OSError, ValidationError) as error:
        code = (
            str(error)
            if isinstance(error, DispatchValidationError)
            else "invalid_input"
        )
        _ = sys.stderr.write(f"migration_dispatch_rejected:{code}\n")
        return 2
    _ = sys.stdout.write("migration_dispatch_validated\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = (
    "DispatchRequest",
    "DispatchValidationError",
    "RunCandidate",
    "canonical_body",
    "select_unique_run",
    "validate_current",
    "validate_database_urls",
    "validate_dispatch",
    "validate_heads",
    "validate_result",
)
