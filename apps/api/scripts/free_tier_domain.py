# pyright: reportUnnecessaryComparison=false, reportUnreachable=false
"""Pure free-tier formulas and schema-closed evidence parsing."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import TYPE_CHECKING, Final, assert_never

import orjson
from pydantic import TypeAdapter

if TYPE_CHECKING:
    from pathlib import Path

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

JSON_OBJECT: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)
MIB: Final = 1024 * 1024
FIXTURE_ROWS: Final = 4_800
FIXTURE_BYTES: Final = 60 * MIB
STRICT_THRESHOLD: Final = 0.70
PROVIDERS: Final = ("github", "vercel-api", "vercel-web", "supabase")
PHASES: Final = ("pre-0010", "post-0010", "acceptance")
ARTIFACT_RETENTION_HOURS: Final[JsonObject] = {
    "activation_evidence": 24,
    "migration_backup_ciphertext": 168,
    "ci_test_build_outputs": 24,
    "local_nonproduction_playwright": 24,
    "rollback_receipts": 168,
    "cadence_receipts": 744,
}
JCS_MAX_SAFE_INTEGER: Final = 9_007_199_254_740_991
JCS_FIXED_MIN: Final = 1e-6
JCS_FIXED_MAX: Final = 1e21


class GateHoldError(RuntimeError):
    """A free-tier claim is incomplete, stale, unsafe, or over threshold."""


def _jcs_integer(number: int) -> bytes:
    if abs(number) > JCS_MAX_SAFE_INTEGER:
        raise GateHoldError("JCS integer exceeds the I-JSON exact range")
    return str(number).encode()


def _jcs_float(number: float) -> bytes:
    if not math.isfinite(number):
        raise GateHoldError("JCS numbers must be finite")
    if number == 0:
        return b"0"
    rendered = orjson.dumps(number).decode()
    if "e" not in rendered:
        return rendered.removesuffix(".0").encode()
    mantissa, exponent_text = rendered.split("e", maxsplit=1)
    exponent = int(exponent_text)
    if JCS_FIXED_MIN <= abs(number) < JCS_FIXED_MAX:
        return format(Decimal(rendered), "f").encode()
    normalized_mantissa = mantissa.removesuffix(".0")
    sign = "+" if exponent >= 0 else ""
    return f"{normalized_mantissa}e{sign}{exponent}".encode()


def _jcs_encode(item: JsonValue) -> bytes:  # noqa: PLR0911
    match item:
        case None:
            return b"null"
        case bool() as boolean:
            return b"true" if boolean else b"false"
        case int() as integer:
            return _jcs_integer(integer)
        case float() as number:
            return _jcs_float(number)
        case str() as text:
            try:
                _ = text.encode("utf-16-be")
            except UnicodeEncodeError as error:
                message = "JCS strings must contain valid Unicode"
                raise GateHoldError(message) from error
            return orjson.dumps(text)
        case list() as items:
            return b"[" + b",".join(_jcs_encode(value) for value in items) + b"]"
        case dict() as mapping:
            try:
                keys = sorted(mapping, key=lambda key: key.encode("utf-16-be"))
            except (AttributeError, UnicodeEncodeError) as error:
                message = "JCS object keys must be valid strings"
                raise GateHoldError(message) from error
            members = (
                orjson.dumps(key) + b":" + _jcs_encode(mapping[key]) for key in keys
            )
            return b"{" + b",".join(members) + b"}"
        case unreachable:
            assert_never(unreachable)


def canonical_bytes(value: JsonObject) -> bytes:
    """Return RFC 8785 JSON Canonicalization Scheme bytes."""
    return _jcs_encode(value)


def sha256_hex(value: bytes) -> str:
    """Return a lowercase SHA-256 digest."""
    return sha256(value).hexdigest()


def with_receipt_sha(value: JsonObject) -> JsonObject:
    """Attach a digest over the canonical body excluding the digest field."""
    if "receipt_sha256" in value:
        raise GateHoldError("receipt body already contains receipt_sha256")
    return {**value, "receipt_sha256": sha256_hex(canonical_bytes(value))}


def require_receipt_sha(value: JsonObject, label: str) -> str:
    """Return a verified canonical receipt digest without exposing its body."""
    receipt = value.get("receipt_sha256")
    if not isinstance(receipt, str):
        raise GateHoldError(f"{label} receipt SHA is required")
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    if receipt != sha256_hex(canonical_bytes(body)):
        raise GateHoldError(f"{label} receipt SHA mismatch")
    return receipt


def fixture_rows() -> tuple[tuple[str, str], ...]:
    """Create exactly 4,800 distinct rows totaling exactly 60 MiB."""
    titles = tuple(f"row-{index:04d}" for index in range(FIXTURE_ROWS))
    body_total = FIXTURE_BYTES - sum(len(title.encode()) for title in titles)
    base_size, remainder = divmod(body_total, FIXTURE_ROWS)
    return tuple(
        (title, "x" * (base_size + (1 if index < remainder else 0)))
        for index, title in enumerate(titles)
    )


def search_projection(actual_bytes: int, raw_amplification: float) -> JsonObject:
    """Project search disk bytes with mandatory floors and one inflation."""
    if actual_bytes < 0 or not math.isfinite(raw_amplification):
        raise GateHoldError("search operands must be known nonnegative values")
    raw_added = math.ceil(
        max(actual_bytes, FIXTURE_BYTES) * max(3.0, raw_amplification)
    )
    return {
        "actual_production_title_body_utf8_bytes": actual_bytes,
        "fixture_floor_bytes": FIXTURE_BYTES,
        "raw_measured_amplification": raw_amplification,
        "raw_added_bytes": raw_added,
        "inflated_added_bytes": math.ceil(1.25 * raw_added),
        "inflation_count": 1,
    }


def page_bound_for_window(
    *,
    capture_at: datetime,
    window_start: datetime,
    window_end: datetime,
    trailing_30d_page_requests: int,
) -> int:
    """Charge full U to every provider window with nonempty horizon overlap."""
    overlap_start = max(capture_at, window_start)
    overlap_end = min(capture_at + timedelta(days=30), window_end)
    if overlap_end <= overlap_start:
        return 0
    return max(10_000, 3 * trailing_30d_page_requests)


def dimension_result(*, observed: int, added_raw: int, quota: int) -> JsonObject:
    """Evaluate one provider dimension using one final inflation."""
    if observed < 0 or added_raw < 0 or quota <= 0:
        raise GateHoldError("dimension operands must be known nonnegative values")
    added = math.ceil(1.25 * added_raw)
    numerator = observed + added
    ratio = numerator / quota
    if ratio >= STRICT_THRESHOLD:
        raise GateHoldError("projected usage must be strictly below 70%")
    return {
        "observed_usage": observed,
        "added_usage_raw": added_raw,
        "added_usage_inflated": added,
        "inflation_count": 1,
        "quota": quota,
        "numerator": numerator,
        "ratio": ratio,
        "accepted": True,
    }


def load_json(path: Path) -> JsonObject:
    """Parse one schema-root JSON object."""
    try:
        return JSON_OBJECT.validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        raise GateHoldError(f"cannot read canonical JSON: {path}") from error


def write_json(path: Path, value: JsonObject) -> None:
    """Write canonical JSON with one trailing newline."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(canonical_bytes(value) + b"\n")


def require_string(value: JsonValue, field: str) -> str:
    """Parse one required nonempty string."""
    if not isinstance(value, str) or not value:
        raise GateHoldError(f"{field} is required")
    return value


def parse_time(value: JsonValue, field: str) -> datetime:
    """Parse one timezone-aware RFC3339 timestamp."""
    if not isinstance(value, str):
        raise GateHoldError(f"{field} must be an RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise GateHoldError(f"{field} must be an RFC3339 timestamp") from error
    if parsed.tzinfo is None:
        raise GateHoldError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)
