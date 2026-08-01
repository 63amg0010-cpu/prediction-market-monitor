# pyright: reportUnnecessaryComparison=false
"""Schema-closed provider quota capture boundary."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import os
import re
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Final, assert_never, cast
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
    require_receipt_sha,
    require_string,
    sha256_hex,
    with_receipt_sha,
)
from apps.api.scripts.free_tier_evidence_contract import (
    CAPTURE_FIELDS,
    DECEMBER,
    DIMENSION_FIELDS,
    MATERIALIZED_CAPTURE_FIELDS,
    OPTIONAL_CHAIN_FIELDS,
    PRIVATE_OBSERVATION_FIELDS,
    PROVIDER_DIMENSIONS,
    PROVIDER_HOSTS,
    PROVIDER_PLANS,
    PROVIDER_PROJECTS,
    PROVIDER_PUBLIC_SOURCE_URLS,
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

SHA256_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
PRIVATE_RESPONSE_FIELDS: Final = frozenset(
    {"schema", "provider", "observation_sha256", "official_payloads"}
)
APPROVAL_LAUNCH_COUNT: Final = 2


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
        or source_url != PROVIDER_PUBLIC_SOURCE_URLS[provider]
    ):
        raise GateHoldError("provider quota source URL is not official")
    _ = _require_sha(capture.get("source_url_sha256"), "source_url_sha256")
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


def _windows_acl_owner_only(path: Path) -> bool:
    """Require a non-inherited full-control DACL for only the current identity."""
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    whoami = system_root / "System32" / "whoami.exe"
    icacls = system_root / "System32" / "icacls.exe"
    try:
        identity = (
            subprocess.run(  # noqa: S603
                [str(whoami)],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
            .stdout.strip()
            .lower()
        )
        sid_output = subprocess.run(  # noqa: S603
            [str(whoami), "/user", "/fo", "csv", "/nh"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        sid_match = re.search(r"S-\d(?:-\d+)+", sid_output, re.IGNORECASE)
        acl_output = subprocess.run(  # noqa: S603
            [str(icacls), str(path.resolve(strict=True))],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    allowed = {identity}
    if sid_match is not None:
        allowed.add(sid_match.group(0).lower())
    resolved = str(path.resolve(strict=True))
    entries: list[tuple[str, str]] = []
    for index, raw_line in enumerate(acl_output.splitlines()):
        line = raw_line.strip()
        if index == 0 and line.lower().startswith(resolved.lower()):
            line = line[len(resolved) :].strip()
        match = re.fullmatch(r"(.+?):((?:\([A-Za-z0-9,]+\))+)", line)
        if match is not None:
            entries.append((match.group(1).strip().lower(), match.group(2)))
    return (
        len(entries) == 1
        and entries[0][0] in allowed
        and "(I)" not in entries[0][1]
        and "(F)" in entries[0][1]
    )


def _require_private_regular_file(path: Path) -> None:
    if path.is_symlink():
        raise GateHoldError("private input symlink is forbidden")
    try:
        status = path.stat()
    except OSError as error:
        raise GateHoldError("private input is unavailable") from error
    if not stat.S_ISREG(status.st_mode) or status.st_size <= 0:
        raise GateHoldError("private input must be a nonempty regular file")
    if os.name == "nt":
        if not _windows_acl_owner_only(path):
            raise GateHoldError("private input Windows ACL is not owner-only")
    else:
        getuid = getattr(os, "getuid", None)
        if (
            getuid is None
            or status.st_uid != getuid()
            or bool(status.st_mode & (stat.S_IRWXG | stat.S_IRWXO))
        ):
            raise GateHoldError(
                "private input POSIX ownership or mode is not owner-only"
            )


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise GateHoldError("private input cannot be hashed") from error
    return digest.hexdigest()


def require_materialization_predecessor(
    predecessor: JsonObject,
    *,
    phase: str,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
) -> str:
    """Validate the exact phase-specific chain node and return its canonical hash."""
    common = (
        predecessor.get("accepted") is True
        and predecessor.get("reviewed_sha") == expected_sha
        and predecessor.get("approved_plan_sha256") == expected_plan_sha256
        and predecessor.get("activation_nonce") == activation_nonce
    )
    if not common:
        raise GateHoldError("provider materialization predecessor binding mismatch")
    command = predecessor.get("command")
    if phase == "pre-0010":
        valid = command == "deployment-prestate"
    elif phase == "post-0010":
        post_fields = {
            "schema_version",
            "command",
            "attempt",
            "reviewed_sha",
            "approved_plan_sha256",
            "approval_round_id",
            "approval_launch_sha256s",
            "activation_nonce",
            "dispatch_nonce",
            "run_id",
            "artifact_sha256",
            "review_root_sha256",
            "no_spend_receipt_sha256",
            "backup_sha256",
            "state_before",
            "state_after",
            "ledger_exists",
            "manifold_data_exists",
            "enum_residue",
            "accepted",
            "terminal_for_attempt",
            "retry_permitted",
            "predecessor_receipt_sha256",
        }
        launches = predecessor.get("approval_launch_sha256s")
        run_id = predecessor.get("run_id")
        attempt = predecessor.get("attempt")
        valid = (
            set(predecessor) == post_fields
            and predecessor.get("schema_version") == 1
            and command == "bootstrap-verify"
            and isinstance(attempt, int)
            and not isinstance(attempt, bool)
            and attempt in {1, 2}
            and predecessor.get("terminal_for_attempt") is True
            and predecessor.get("retry_permitted") is False
            and predecessor.get("state_before") == "20260726_0009"
            and predecessor.get("state_after") == "20260727_0010"
            and predecessor.get("ledger_exists") is True
            and predecessor.get("manifold_data_exists") is False
            and predecessor.get("enum_residue") is False
            and isinstance(run_id, int)
            and not isinstance(run_id, bool)
            and run_id > 0
            and isinstance(launches, list)
            and len(launches) == APPROVAL_LAUNCH_COUNT
            and all(
                isinstance(value, str) and SHA256_PATTERN.fullmatch(value)
                for value in (
                    predecessor.get("approval_round_id"),
                    *launches,
                    predecessor.get("artifact_sha256"),
                    predecessor.get("review_root_sha256"),
                    predecessor.get("no_spend_receipt_sha256"),
                    predecessor.get("backup_sha256"),
                    predecessor.get("predecessor_receipt_sha256"),
                )
            )
        )
    elif phase == "acceptance":
        _ = require_receipt_sha(predecessor, "acceptance predecessor")
        acceptance_fields = {
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
            "details",
            "receipt_sha256",
        }
        launches = predecessor.get("approval_launch_sha256s")
        timestamps = predecessor.get("database_timestamps")
        details = predecessor.get("details")
        detail_hashes = (
            (
                details.get("fan_in_sha256"),
                details.get("cadence_sha256"),
                details.get("f4_sha256"),
            )
            if isinstance(details, dict)
            else ()
        )
        status = details.get("status") if isinstance(details, dict) else None
        refresh_sha = (
            details.get("acceptance_refresh_sha256")
            if isinstance(details, dict)
            else None
        )
        valid = (
            set(predecessor) == acceptance_fields
            and predecessor.get("schema") == "release-chain-receipt.v1"
            and command == "aggregate"
            and predecessor.get("dispatch_nonce") is None
            and predecessor.get("attempt") == 0
            and predecessor.get("terminal_for_attempt") is True
            and predecessor.get("retry_permitted") is False
            and isinstance(launches, list)
            and len(launches) == APPROVAL_LAUNCH_COUNT
            and isinstance(timestamps, dict)
            and set(timestamps) == {"created_at_db"}
            and isinstance(timestamps.get("created_at_db"), str)
            and isinstance(details, dict)
            and set(details)
            == {
                "status",
                "fan_in_sha256",
                "cadence_sha256",
                "f4_sha256",
                "acceptance_refresh_sha256",
            }
            and status in {"HOLD", "COMPLETE"}
            and all(
                isinstance(value, str) and SHA256_PATTERN.fullmatch(value)
                for value in (
                    predecessor.get("approval_round_id"),
                    *launches,
                    predecessor.get("predecessor_receipt_sha256"),
                    *detail_hashes,
                )
            )
            and (
                (status == "HOLD" and refresh_sha is None)
                or (
                    status == "COMPLETE"
                    and isinstance(refresh_sha, str)
                    and SHA256_PATTERN.fullmatch(refresh_sha)
                )
            )
        )
    else:
        valid = False
    if not valid:
        raise GateHoldError("provider materialization predecessor is invalid")
    return sha256_hex(canonical_bytes(predecessor))


def _validate_materialized_capture(
    capture: JsonObject,
    *,
    provider: str,
    identity_envs: tuple[str, ...],
) -> None:
    if frozenset(capture) != CAPTURE_FIELDS:
        raise GateHoldError("provider capture schema is not closed")
    if capture.get("provider") != provider:
        raise GateHoldError("provider mismatch")
    if capture.get("identity_sha256") != identity_digest(identity_envs):
        raise GateHoldError("protected provider identity mismatch")
    if capture.get("identity_bindings") != identity_bindings(identity_envs):
        raise GateHoldError("protected provider identity binding mismatch")
    verified = with_receipt_sha(
        {
            **capture,
            "schema": "free-tier.provider-capture-verified.v1",
            "phase": "pre-0010",
            "reviewed_sha": "0" * 40,
            "input_sha256": sha256_hex(canonical_bytes(capture)),
        }
    )
    _ = validate_verified_capture(verified)


def materialize_provider_capture(
    *,
    provider: str,
    observation_path: Path,
    raw_response_path: Path,
    screenshot_path: Path,
    identity_envs: tuple[str, ...],
) -> JsonObject:
    """Derive one redacted provider capture from owner-only inputs."""
    if provider not in PROVIDERS:
        raise GateHoldError("provider is unsupported")
    require_provider_identity_envs(provider, identity_envs)
    for path in (observation_path, raw_response_path, screenshot_path):
        _require_private_regular_file(path)
    observation = load_json(observation_path)
    if frozenset(observation) != PRIVATE_OBSERVATION_FIELDS:
        raise GateHoldError("private provider observation schema is not closed")
    if observation.get("schema") != "free-tier.provider-observation.v1":
        raise GateHoldError("private provider observation schema mismatch")
    if observation.get("provider") != provider:
        raise GateHoldError("provider mismatch")
    response = load_json(raw_response_path)
    payloads = response.get("official_payloads")
    if (
        frozenset(response) != PRIVATE_RESPONSE_FIELDS
        or response.get("schema") != "free-tier.provider-private-response.v1"
        or response.get("provider") != provider
        or response.get("observation_sha256")
        != sha256_hex(canonical_bytes(observation))
        or not isinstance(payloads, list)
        or not payloads
    ):
        raise GateHoldError("private provider response does not derive observation")
    source_url = require_string(observation.get("source_url"), "source_url")
    parsed_source = urlsplit(source_url)
    if (
        parsed_source.scheme != "https"
        or parsed_source.hostname not in PROVIDER_HOSTS[provider]
        or parsed_source.username is not None
        or parsed_source.password is not None
    ):
        raise GateHoldError("private provider source URL is not official")
    capture: JsonObject = {
        **observation,
        "schema": "free-tier.provider-capture.v1",
        "source_url": PROVIDER_PUBLIC_SOURCE_URLS[provider],
        "identity_sha256": identity_digest(identity_envs),
        "identity_bindings": cast("JsonValue", identity_bindings(identity_envs)),
        "response_sha256": _file_sha256(raw_response_path),
        "screenshot_sha256": _file_sha256(screenshot_path),
        "source_url_sha256": sha256_hex(source_url.encode()),
    }
    _validate_materialized_capture(
        capture,
        provider=provider,
        identity_envs=identity_envs,
    )
    return capture


def import_provider_capture(  # noqa: PLR0913
    *,
    provider: str,
    input_path: Path,
    identity_envs: tuple[str, ...],
    expected_sha: str,
    phase: str,
    expected_plan_sha256: str = "",
    activation_nonce: str = "",
    predecessor: JsonObject | None = None,
) -> JsonObject:
    """Validate and minimize one redacted provider capture."""
    if provider not in PROVIDERS or phase not in PHASES:
        raise GateHoldError("unsupported provider or phase")
    require_provider_identity_envs(provider, identity_envs)
    if predecessor is None:
        raise GateHoldError("deployment root receipt is required")
    predecessor_sha = require_materialization_predecessor(
        predecessor,
        phase=phase,
        expected_sha=expected_sha,
        expected_plan_sha256=expected_plan_sha256,
        activation_nonce=activation_nonce,
    )
    materialized = load_json(input_path)
    materialization_sha = require_receipt_sha(materialized, "materialization")
    if (
        frozenset(materialized) != MATERIALIZED_CAPTURE_FIELDS
        or materialized.get("schema") != "free-tier.provider-capture-materialized.v1"
        or materialized.get("reviewed_sha") != expected_sha
        or materialized.get("approved_plan_sha256") != expected_plan_sha256
        or materialized.get("activation_nonce") != activation_nonce
        or materialized.get("phase") != phase
        or materialized.get("predecessor_receipt_sha256") != predecessor_sha
    ):
        raise GateHoldError("provider materialization root binding mismatch")
    raw_value = materialized.get("capture")
    if not isinstance(raw_value, dict):
        raise GateHoldError("materialized provider capture is required")
    raw = cast("JsonObject", raw_value)
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
            "materialization_predecessor_sha256": predecessor_sha,
            "materialization_receipt_sha256": materialization_sha,
            "input_sha256": sha256_hex(canonical_bytes(raw)),
        }
    )
    _ = validate_verified_capture(receipt)
    return receipt
