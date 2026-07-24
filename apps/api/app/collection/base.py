"""Shared typed errors and cryptographic collection primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum, unique
from hashlib import sha256
from hmac import compare_digest
from typing import TYPE_CHECKING, ClassVar, Final, override

from pydantic import ConfigDict, RootModel

from app.domain.types import JsonValue
from app.services.configuration.canonical import canonical_bytes

if TYPE_CHECKING:
    from uuid import UUID

MAX_POST_BYTES: Final[int] = 256 * 1024
MAX_COMMAND_ATTEMPTS: Final[int] = 3
RESERVATION_STALE_SECONDS: Final[int] = 3 * 60
EXECUTION_STALE_SECONDS: Final[int] = 6 * 60


@unique
class CollectionErrorCode(StrEnum):
    """Stable machine-consumed collection failure codes."""

    INVALID_CONTRACT = "invalid_contract"
    SOURCE_AUTHORIZATION_INACTIVE = "source_authorization_inactive"
    INVALID_TRANSITION = "invalid_transition"
    COMMAND_NOT_AVAILABLE = "command_not_available"
    RETRIES_EXHAUSTED = "retries_exhausted"
    RESERVATION_ACTIVE = "reservation_active"
    LEASE_OR_ATTEMPT_MISMATCH = "lease_or_attempt_mismatch"
    CHECKPOINT_CONFLICT = "checkpoint_conflict"
    ORDINAL_GAP = "ordinal_gap"
    ORDINAL_ALREADY_COMMITTED = "ordinal_already_committed"
    RUN_STREAM_SEALED = "run_stream_sealed"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    INVALID_TERMINAL_REASON = "invalid_terminal_reason"
    TERMINAL_PAGE_MISSING = "terminal_page_missing"
    COMPLETION_MISMATCH = "completion_mismatch"
    COMPLETION_IDEMPOTENCY_MISMATCH = "completion_idempotency_mismatch"
    RUN_SET_MISMATCH = "run_set_mismatch"


@dataclass(frozen=True, slots=True)
class CollectionError(Exception):
    """A typed collection failure safe for boundary translation."""

    code: CollectionErrorCode
    status_code: int
    current_checkpoint_revision: int | None = None
    current_cursor: str | None = None
    expected_page_ordinal: int | None = None
    existing_commit_id: UUID | None = None

    @override
    def __str__(self) -> str:
        return self.code.value


class _CanonicalValue(RootModel[JsonValue]):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


def require_utc(value: datetime) -> datetime:
    """Return a UTC timestamp or reject a naive clock value."""
    if value.tzinfo is None:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Format canonical RFC 3339 UTC with six fractional digits."""
    return require_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def hash_token(value: str) -> bytes:
    """Hash a plaintext lease or nonce for durable comparison."""
    return sha256(value.encode("utf-8")).digest()


def token_matches(value: str, expected_hash: bytes | None) -> bool:
    """Constant-time compare a plaintext token with a stored digest."""
    return expected_hash is not None and compare_digest(
        hash_token(value), expected_hash
    )


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Serialize a typed JSON value with the project's canonical rules."""
    return canonical_bytes(_CanonicalValue(value))


def canonical_json_hash(value: JsonValue) -> str:
    """Hash a typed canonical JSON value with SHA-256."""
    return sha256(canonical_json_bytes(value)).hexdigest()
