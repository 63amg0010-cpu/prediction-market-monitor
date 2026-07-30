"""Common canonical and provenance boundaries for release-chain handlers."""

# ruff: noqa: D102, EM101, EM102, PLR2004, TC003

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Final, Protocol, cast
from uuid import UUID

from app.services.release.receipts import canonicalize
from pydantic import TypeAdapter

type JsonScalar = str | int | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
Clock = Callable[[], datetime]
DOCUMENT: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)

COMMON_FIELDS: Final = frozenset(
    {
        "schema",
        "command",
        "reviewed_sha",
        "approved_plan_sha256",
        "approval_round_id",
        "approval_launch_sha256s",
        "activation_nonce",
        "dispatch_nonce",
        "attempt",
        "database_timestamps",
        "accepted",
        "terminal_for_attempt",
        "retry_permitted",
        "predecessor_receipt_sha256",
        "receipt_sha256",
    }
)
SENSITIVE_KEYS: Final = ("secret", "token", "password", "credential", "raw_", "dom")


class ReleaseChainError(RuntimeError):
    """The supplied evidence cannot form a release chain."""


class ReceiptIO(Protocol):
    """Injected filesystem boundary used by handlers."""

    def read(self, path: Path) -> bytes: ...

    def write(self, path: Path, value: bytes) -> None: ...


class PathReceiptIO:
    """Local canonical receipt storage with no network behavior."""

    def read(self, path: Path) -> bytes:
        return path.read_bytes()

    def write(self, path: Path, value: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _ = path.write_bytes(value)


@dataclass(frozen=True, slots=True)
class Bindings:
    """Immutable approval and activation bindings copied by successors."""

    reviewed_sha: str
    approved_plan_sha256: str
    approval_round_id: str
    approval_launch_sha256s: tuple[str, str]
    activation_nonce: str


def utc_now() -> datetime:
    """Return a timezone-aware clock value."""
    return datetime.now(tz=UTC)


def digest(value: bytes) -> str:
    """Return one lowercase SHA-256 digest."""
    return sha256(value).hexdigest()


def load_document(
    io: ReceiptIO,
    path: Path,
    *,
    allow_trailing_newline: bool = False,
) -> JsonObject:
    """Read one canonical JSON object and reject duplicate keys."""
    raw = io.read(path)
    try:
        parsed = cast("object", json.loads(raw, object_pairs_hook=_unique_object))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseChainError("invalid_json") from error
    if not isinstance(parsed, dict):
        raise ReleaseChainError("json_root_not_object")
    document = DOCUMENT.validate_python(parsed)
    canonical = canonicalize(document)
    if raw != canonical and (not allow_trailing_newline or raw != canonical + b"\n"):
        raise ReleaseChainError("noncanonical_json")
    return document


def verified_receipt(io: ReceiptIO, path: Path) -> JsonObject:
    """Read and verify one foundation-compatible canonical receipt."""
    document = load_document(io, path)
    missing = COMMON_FIELDS - document.keys()
    if missing:
        raise ReleaseChainError(f"receipt_missing_fields:{','.join(sorted(missing))}")
    body = {key: value for key, value in document.items() if key != "receipt_sha256"}
    if document["receipt_sha256"] != digest(canonicalize(body)):
        raise ReleaseChainError("receipt_hash_mismatch")
    timestamps = document["database_timestamps"]
    if not isinstance(timestamps, dict) or "created_at_db" not in timestamps:
        raise ReleaseChainError("receipt_database_timestamps_invalid")
    if not isinstance(document["accepted"], bool):
        raise ReleaseChainError("receipt_accepted_invalid")
    if document["accepted"] and document["retry_permitted"]:
        raise ReleaseChainError("accepted_receipt_cannot_retry")
    if document["retry_permitted"] and not document["terminal_for_attempt"]:
        raise ReleaseChainError("retry_requires_terminal_attempt")
    return document


def bindings_of(receipt: Mapping[str, JsonValue]) -> Bindings:
    """Extract and minimally validate inherited receipt bindings."""
    launches = receipt.get("approval_launch_sha256s")
    if (
        not isinstance(launches, list)
        or len(launches) != 2
        or not all(_is_hex(item, 64) for item in launches)
    ):
        raise ReleaseChainError("approval_launch_bindings_invalid")
    values = (
        _string(receipt, "reviewed_sha"),
        _string(receipt, "approved_plan_sha256"),
        _string(receipt, "approval_round_id"),
        (str(launches[0]), str(launches[1])),
        _string(receipt, "activation_nonce"),
    )
    if not _is_hex(values[0], 40) or not all(
        _is_hex(value, 64) for value in (values[1], values[2], *values[3])
    ):
        raise ReleaseChainError("approval_bindings_invalid")
    try:
        _ = UUID(values[4])
    except ValueError as error:
        raise ReleaseChainError("activation_nonce_invalid") from error
    return Bindings(*values)


def require_bindings(
    receipt: Mapping[str, JsonValue],
    expected: Bindings,
) -> None:
    """Reject foreign review, plan, launch, or activation provenance."""
    if bindings_of(receipt) != expected:
        raise ReleaseChainError("foreign_receipt_bindings")


def build_receipt(
    *,
    command: str,
    predecessor: JsonObject,
    clock: Clock,
    details: JsonObject,
    attempt: int = 0,
) -> JsonObject:
    """Build one schema-closed, redacted successor receipt."""
    bindings = bindings_of(predecessor)
    predecessor_sha = _string(predecessor, "receipt_sha256")
    body: JsonObject = {
        "schema": "release-chain-receipt.v1",
        "command": command,
        "reviewed_sha": bindings.reviewed_sha,
        "approved_plan_sha256": bindings.approved_plan_sha256,
        "approval_round_id": bindings.approval_round_id,
        "approval_launch_sha256s": list(bindings.approval_launch_sha256s),
        "activation_nonce": bindings.activation_nonce,
        "dispatch_nonce": None,
        "attempt": attempt,
        "database_timestamps": {"created_at_db": _timestamp(clock())},
        "accepted": True,
        "terminal_for_attempt": True,
        "retry_permitted": False,
        "predecessor_receipt_sha256": predecessor_sha,
        "details": details,
    }
    _require_redacted(body)
    return {**body, "receipt_sha256": digest(canonicalize(body))}


def write_receipt(io: ReceiptIO, path: Path, receipt: JsonObject) -> None:
    """Write exactly canonical receipt bytes without a trailing newline."""
    io.write(path, canonicalize(receipt))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseChainError("duplicate_json_key")
        result[key] = value
    return result


def _string(value: Mapping[str, JsonValue], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise ReleaseChainError(f"{field}_invalid")
    return item


def _is_hex(value: object, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in "0123456789abcdef" for character in value)
    )


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ReleaseChainError("clock_not_timezone_aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _require_redacted(value: JsonValue, key: str = "") -> None:
    if any(part in key.lower() for part in SENSITIVE_KEYS):
        raise ReleaseChainError("output_contains_sensitive_field")
    if isinstance(value, str) and ("://" in value or "bearer " in value.lower()):
        raise ReleaseChainError("output_contains_sensitive_value")
    if isinstance(value, list):
        for item in value:
            _require_redacted(item, key)
    elif isinstance(value, dict):
        for child_key, item in value.items():
            _require_redacted(item, child_key)
