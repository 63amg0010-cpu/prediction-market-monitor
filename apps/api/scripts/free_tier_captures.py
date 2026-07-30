# pyright: reportUnnecessaryComparison=false
"""Schema-closed provider quota capture boundary."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final, assert_never
from urllib.parse import urlsplit

from apps.api.scripts.free_tier_domain import (
    PHASES,
    PROVIDERS,
    GateHoldError,
    JsonObject,
    JsonValue,
    canonical_bytes,
    load_json,
    parse_time,
    require_string,
    sha256_hex,
    with_receipt_sha,
)
from apps.api.scripts.free_tier_evidence_contract import (
    CAPTURE_FIELDS,
    DECEMBER,
    DIMENSION_FIELDS,
    OPTIONAL_CHAIN_FIELDS,
    PROVIDER_DIMENSIONS,
    PROVIDER_HOSTS,
    PROVIDER_PLANS,
    PROVIDER_PROJECTS,
    VERIFIED_FIELDS,
)
from apps.api.scripts.free_tier_identities import (
    identity_bindings,
    require_identity_bindings,
    require_provider_identity_envs,
)
from apps.api.scripts.free_tier_projection import (
    derive_added_usage_raw,
    expected_window_ids,
)

if TYPE_CHECKING:
    from pathlib import Path

SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")


class _WindowKind(StrEnum):
    ROLLING_HOUR = "rolling-hour"
    ROLLING_DAY = "rolling-day"
    ROLLING_WEEK = "rolling-week"
    BILLING_MONTH = "billing-month"


def identity_digest(identity_envs: tuple[str, ...]) -> str:
    """Hash protected identities without returning their values."""
    if not identity_envs:
        raise GateHoldError("at least one --identity-env is required")
    values: list[str] = []
    for name in identity_envs:
        value = os.environ.get(name)
        if value is None or not value:
            raise GateHoldError(f"protected identity environment is empty: {name}")
        values.append(value)
    return sha256_hex("\0".join(values).encode())


def _require_sha(value: JsonValue, field: str) -> str:
    digest = require_string(value, field)
    if SHA256_PATTERN.fullmatch(digest) is None:
        raise GateHoldError(f"{field} must be a lowercase SHA-256")
    return digest


def _require_real_window(dimension: JsonObject, captured_at: datetime) -> str:
    start = parse_time(dimension.get("window_start"), "dimension.window_start")
    end = parse_time(dimension.get("window_end"), "dimension.window_end")
    horizon_end = captured_at + timedelta(days=30)
    if start >= end or end <= captured_at or start >= horizon_end:
        raise GateHoldError("quota window does not intersect the horizon")
    try:
        kind = _WindowKind(
            require_string(dimension.get("window_kind"), "dimension.window_kind")
        )
    except ValueError as error:
        raise GateHoldError("quota window kind is unsupported") from error
    aligned = start.minute == start.second == start.microsecond == 0
    match kind:
        case _WindowKind.ROLLING_HOUR:
            valid = aligned and end - start == timedelta(hours=1)
        case _WindowKind.ROLLING_DAY:
            valid = aligned and start.hour == 0 and end - start == timedelta(days=1)
        case _WindowKind.ROLLING_WEEK:
            valid = (
                aligned
                and start.hour == 0
                and start.weekday() == 0
                and end - start == timedelta(days=7)
            )
        case _WindowKind.BILLING_MONTH:
            next_month = (
                datetime(start.year + 1, 1, 1, tzinfo=UTC)
                if start.month == DECEMBER
                else datetime(start.year, start.month + 1, 1, tzinfo=UTC)
            )
            valid = aligned and start.hour == 0 and start.day == 1 and end == next_month
        case unreachable:
            assert_never(unreachable)
    if not valid:
        raise GateHoldError("quota window boundaries are not provider-aligned")
    expected_id = (
        f"{kind.value}:{start.isoformat().replace('+00:00', 'Z')}:"
        f"{end.isoformat().replace('+00:00', 'Z')}"
    )
    if dimension.get("window_id") != expected_id:
        raise GateHoldError("quota window id mismatch")
    return expected_id


def _provider_metadata(capture: JsonObject) -> tuple[str, datetime]:
    keys = set(capture)
    if frozenset(keys - OPTIONAL_CHAIN_FIELDS) != VERIFIED_FIELDS:
        raise GateHoldError("verified provider capture schema is not closed")
    if capture.get("schema") != "free-tier.provider-capture-verified.v1":
        raise GateHoldError("verified provider capture schema mismatch")
    provider = require_string(capture.get("provider"), "provider")
    if provider not in PROVIDERS:
        raise GateHoldError("provider is unsupported")
    if capture.get("public_project") != PROVIDER_PROJECTS[provider]:
        raise GateHoldError("provider public project mismatch")
    _ = _require_sha(capture.get("identity_sha256"), "identity_sha256")
    require_identity_bindings(capture, provider)
    captured_at = parse_time(capture.get("captured_at"), "captured_at")
    if capture.get("plan") != PROVIDER_PLANS[provider]:
        raise GateHoldError("paid or unknown provider plan")
    if capture.get("paid_enabled") is not False:
        raise GateHoldError("paid provider path is enabled")
    if capture.get("overage_enabled") is not False:
        raise GateHoldError("provider overage path is enabled")
    if capture.get("quota_status") != "known":
        raise GateHoldError("unknown or N/A quota is unsupported")
    return provider, captured_at


def _require_official_source(capture: JsonObject, provider: str) -> None:
    if capture.get("source_url_class") != "official-provider-api-or-dashboard":
        raise GateHoldError("provider quota source is not official")
    source_url = require_string(capture.get("source_url"), "source_url")
    parsed_source = urlsplit(source_url)
    if (
        parsed_source.scheme != "https"
        or parsed_source.hostname not in PROVIDER_HOSTS[provider]
    ):
        raise GateHoldError("provider quota source URL is not official")
    if capture.get("source_url_sha256") != sha256_hex(source_url.encode()):
        raise GateHoldError("provider quota source URL hash mismatch")
    for field in (
        "response_sha256",
        "screenshot_sha256",
        "input_sha256",
        "receipt_sha256",
    ):
        _ = _require_sha(capture.get(field), field)


def _provider_dimensions(  # noqa: C901
    capture: JsonObject,
    provider: str,
    captured_at: datetime,
) -> list[JsonObject]:
    dimensions_value = capture.get("dimensions")
    if not isinstance(dimensions_value, list):
        raise GateHoldError("provider dimensions are required")
    dimensions: list[JsonObject] = []
    names: set[str] = set()
    windows_by_name: dict[str, set[str]] = {}
    kind_by_name: dict[str, str] = {}
    for value in dimensions_value:
        if not isinstance(value, dict) or frozenset(value) != DIMENSION_FIELDS:
            raise GateHoldError("provider dimension schema is not closed")
        name = require_string(value.get("name"), "dimension.name")
        names.add(name)
        kind = require_string(value.get("window_kind"), "dimension.window_kind")
        existing_kind = kind_by_name.setdefault(name, kind)
        if existing_kind != kind:
            raise GateHoldError("provider dimension window kind drift")
        for field in ("observed_usage", "added_usage_raw", "quota"):
            operand = value.get(field)
            if not isinstance(operand, int) or isinstance(operand, bool):
                raise GateHoldError(f"dimension operand is unknown: {name}.{field}")
        if value.get("status") != "known":
            raise GateHoldError("unknown or N/A dimension is unsupported")
        window_id = _require_real_window(value, captured_at)
        seen_windows = windows_by_name.setdefault(name, set())
        if window_id in seen_windows:
            raise GateHoldError("provider dimension window is duplicated")
        seen_windows.add(window_id)
        derived = derive_added_usage_raw(value, capture["captured_at"])
        if value.get("added_usage_raw") != derived:
            raise GateHoldError("added usage does not match projection operands")
        dimensions.append(value)
    if frozenset(names) != PROVIDER_DIMENSIONS[provider]:
        raise GateHoldError("provider dimension set is incomplete or unexpected")
    for name, kind in kind_by_name.items():
        if windows_by_name[name] != set(
            expected_window_ids(kind=kind, captured_at=capture["captured_at"])
        ):
            raise GateHoldError("provider dimension window set is incomplete")
    return dimensions


def validate_verified_capture(capture: JsonObject) -> tuple[str, list[JsonObject]]:
    """Parse a content-addressed verified capture into trusted dimensions."""
    provider, captured_at = _provider_metadata(capture)
    _require_official_source(capture, provider)
    dimensions = _provider_dimensions(capture, provider, captured_at)
    return provider, dimensions


def import_provider_capture(
    *,
    provider: str,
    input_path: Path,
    identity_envs: tuple[str, ...],
    expected_sha: str,
    phase: str,
) -> JsonObject:
    """Validate and minimize one redacted provider capture."""
    if provider not in PROVIDERS or phase not in PHASES:
        raise GateHoldError("unsupported provider or phase")
    require_provider_identity_envs(provider, identity_envs)
    raw = load_json(input_path)
    if frozenset(raw) != CAPTURE_FIELDS:
        raise GateHoldError("provider capture schema is not closed")
    if raw.get("schema") != "free-tier.provider-capture.v1":
        raise GateHoldError("provider capture schema mismatch")
    if raw.get("provider") != provider:
        raise GateHoldError("provider mismatch")
    if raw.get("identity_sha256") != identity_digest(identity_envs):
        raise GateHoldError("protected provider identity mismatch")
    if raw.get("identity_bindings") != identity_bindings(identity_envs):
        raise GateHoldError("protected provider identity binding mismatch")
    receipt = with_receipt_sha(
        {
            **raw,
            "schema": "free-tier.provider-capture-verified.v1",
            "phase": phase,
            "reviewed_sha": expected_sha,
            "input_sha256": sha256_hex(canonical_bytes(raw)),
        }
    )
    _ = validate_verified_capture(receipt)
    return receipt
