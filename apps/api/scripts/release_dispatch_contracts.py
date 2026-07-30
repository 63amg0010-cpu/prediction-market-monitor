"""Canonical, redacted contracts for immutable release dispatch helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Never, Protocol, cast

from app.domain.types import JsonValue
from pydantic import TypeAdapter, ValidationError

JsonObject = dict[str, JsonValue]
MAX_RECEIPT_BYTES = 8192
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_PREFIX = r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
_UUID_SUFFIX = r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
UUID_RE = re.compile(f"{_UUID_PREFIX}{_UUID_SUFFIX}")
_REDACTED_FIELD_PATTERN = (
    r"(?i)(authorization|bearer|database_url|password|secret|token)="
)
SECRET_RE = re.compile(f"{_REDACTED_FIELD_PATTERN}|postgres(?:ql)?://")
CHAIN_FIELDS = (
    "reviewed_sha",
    "approved_plan_sha256",
    "approval_round_id",
    "approval_launch_sha256s",
    "activation_nonce",
)
APPROVAL_LAUNCH_COUNT = 2
_JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class HoldError(RuntimeError):
    """Stable fail-closed error that never includes transported values."""


@dataclass(frozen=True, slots=True)
class ChildResult:
    """Bounded result returned by an injected child process adapter."""

    returncode: int
    stdout: str
    stderr: str


class ChildRunner(Protocol):
    """One-shot child runner; production subprocess policy lives at the edge."""

    def run(
        self, argv: tuple[str, ...], stdin: bytes | None = None
    ) -> ChildResult:
        """Execute one already-validated argv with optional protected stdin."""
        ...


def hold(code: str) -> Never:
    """Raise one redacted HOLD code."""
    raise HoldError(code)


def canonical_bytes(value: JsonObject) -> bytes:
    """Encode the integer/string/bool/null JSON subset deterministically."""
    _reject_unsupported(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _reject_unsupported(value: object) -> None:
    if isinstance(value, float):
        hold("noncanonical_number")
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        if not all(isinstance(key, str) for key in mapping):
            hold("noncanonical_key")
        for item in mapping.values():
            _reject_unsupported(item)
    elif isinstance(value, (list, tuple)):
        sequence = cast("list[object] | tuple[object, ...]", value)
        for item in sequence:
            _reject_unsupported(item)
    elif value is not None and not isinstance(value, (str, int, bool)):
        hold("noncanonical_value")


def sha256_hex(value: bytes) -> str:
    """Return a lowercase SHA-256 digest."""
    return sha256(value).hexdigest()


def argv_sha256(argv: tuple[str, ...]) -> str:
    """Hash argv without placing it in a public receipt."""
    return sha256_hex(b"\0".join(part.encode() for part in argv))


def load_canonical(raw: bytes, *, max_bytes: int = MAX_RECEIPT_BYTES) -> JsonObject:
    """Parse one bounded canonical object and reject schema transport tricks."""
    if not raw or len(raw) > max_bytes:
        hold("receipt_size_invalid")
    try:
        parsed = _JSON_ADAPTER.validate_json(raw)
    except ValidationError as error:
        error_code = "receipt_json_invalid"
        raise HoldError(error_code) from error
    if not isinstance(parsed, dict):
        hold("receipt_object_required")
    value = parsed
    if canonical_bytes(value) != raw:
        hold("receipt_not_canonical")
    if SECRET_RE.search(raw.decode(errors="ignore")):
        hold("receipt_secret_forbidden")
    return value


def validate_common(
    value: JsonObject,
    *,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
) -> None:
    """Validate immutable review, plan, launch, and activation bindings."""
    if SHA_RE.fullmatch(expected_sha) is None:
        hold("expected_sha_invalid")
    if SHA256_RE.fullmatch(expected_plan_sha256) is None:
        hold("expected_plan_sha256_invalid")
    if UUID_RE.fullmatch(activation_nonce) is None:
        hold("activation_nonce_invalid")
    launches = value.get("approval_launch_sha256s")
    valid_launches = (
        isinstance(launches, list)
        and len(launches) == APPROVAL_LAUNCH_COUNT
        and all(
            isinstance(item, str) and SHA256_RE.fullmatch(item)
            for item in launches
        )
    )
    if (
        value.get("reviewed_sha") != expected_sha
        or value.get("approved_plan_sha256") != expected_plan_sha256
        or value.get("activation_nonce") != activation_nonce
        or not isinstance(value.get("approval_round_id"), str)
        or SHA256_RE.fullmatch(str(value.get("approval_round_id"))) is None
        or not valid_launches
    ):
        hold("receipt_binding_mismatch")


def copied_chain_fields(value: JsonObject) -> JsonObject:
    """Copy only immutable approval-chain authority fields."""
    return {field: value[field] for field in CHAIN_FIELDS}


def run_once(runner: ChildRunner, argv: tuple[str, ...]) -> ChildResult:
    """Execute exactly once and reduce all failures to a redacted code."""
    result = runner.run(argv)
    if result.returncode != 0:
        hold("child_failed")
    if SECRET_RE.search(result.stderr):
        hold("child_error_secret_forbidden")
    return result


__all__ = (
    "ChildResult",
    "ChildRunner",
    "HoldError",
    "JsonObject",
    "argv_sha256",
    "canonical_bytes",
    "copied_chain_fields",
    "hold",
    "load_canonical",
    "run_once",
    "sha256_hex",
    "validate_common",
)
