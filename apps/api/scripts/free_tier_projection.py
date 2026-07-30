"""Derive provider quota additions from closed primitive operands."""

# ruff: noqa: EM101, EM102, PLR2004, TRY003

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil

from apps.api.scripts.free_tier_capture_contract import (
    ARTIFACT_FIELDS,
    ATTEMPT_FIELDS,
    BACKUP_FIELDS,
    DEPLOYMENT_FIELDS,
    PROJECTION_OPERAND_FIELDS,
    REQUIRED_ARTIFACT_CATEGORIES,
    REQUIRED_DEPLOYMENT_OPERATIONS,
    REQUIRED_WORKFLOW_KINDS,
    TRAFFIC_FIELDS,
)
from apps.api.scripts.free_tier_domain import (
    GateHoldError,
    JsonObject,
    JsonValue,
    page_bound_for_window,
    parse_time,
)

GIB = 1024 * 1024 * 1024
HORIZON = timedelta(days=30)


def _uint(value: JsonValue, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise GateHoldError(f"projection operand is unknown: {field}")
    return value


def _object(value: JsonValue, fields: frozenset[str], label: str) -> JsonObject:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise GateHoldError(f"{label} projection schema is not closed")
    return value


def _records(value: JsonValue, fields: frozenset[str], label: str) -> list[JsonObject]:
    if not isinstance(value, list):
        raise GateHoldError(f"{label} projection records are required")
    return [_object(record, fields, label) for record in value]


def _require_nonempty_set(
    values: set[str],
    required: frozenset[str],
    label: str,
) -> None:
    if values != set(required):
        raise GateHoldError(f"projection {label} set is incomplete")


def _inside_window(record: JsonObject, start: JsonValue, end: JsonValue) -> bool:
    possible_at = parse_time(record.get("possible_at"), "projection.possible_at")
    window_start = parse_time(start, "dimension.window_start")
    window_end = parse_time(end, "dimension.window_end")
    return window_start <= possible_at < window_end


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _floor_day(value: datetime) -> datetime:
    return datetime(value.year, value.month, value.day, tzinfo=UTC)


def _floor_week(value: datetime) -> datetime:
    return _floor_day(value) - timedelta(days=value.weekday())


def _floor_month(value: datetime) -> datetime:
    return datetime(value.year, value.month, 1, tzinfo=UTC)


def _next_month(value: datetime) -> datetime:
    if value.month == 12:
        return datetime(value.year + 1, 1, 1, tzinfo=UTC)
    return datetime(value.year, value.month + 1, 1, tzinfo=UTC)


def _window_id(kind: str, start: datetime, end: datetime) -> str:
    start_text = start.isoformat().replace("+00:00", "Z")
    end_text = end.isoformat().replace("+00:00", "Z")
    return f"{kind}:{start_text}:{end_text}"


def _next_fixed_window(current: datetime, step: timedelta | None) -> datetime:
    return _next_month(current) if step is None else current + step


def expected_window_ids(*, kind: str, captured_at: JsonValue) -> frozenset[str]:
    """Enumerate every aligned provider window intersecting the 30-day horizon."""
    capture = parse_time(captured_at, "captured_at")
    horizon_end = capture + HORIZON
    match kind:
        case "rolling-hour":
            start = _floor_hour(capture)
            step = timedelta(hours=1)
        case "rolling-day":
            start = _floor_day(capture)
            step = timedelta(days=1)
        case "rolling-week":
            start = _floor_week(capture)
            step = timedelta(days=7)
        case "billing-month":
            start = _floor_month(capture)
            step = None
        case _:
            raise GateHoldError("quota window kind is unsupported")
    windows: set[str] = set()
    current = start
    while current < horizon_end:
        end = _next_fixed_window(current, step)
        if end > capture:
            windows.add(_window_id(kind, current, end))
        current = end
    return frozenset(windows)


def _retention_units(record: JsonObject, byte_field: str) -> int:
    return ceil(
        _uint(record[byte_field], byte_field)
        * _uint(record["attempts"], "attempts")
        * _uint(record["retention_hours"], "retention_hours")
        * _uint(record["units_per_gib_hour"], "units_per_gib_hour")
        / GIB
    )


def derive_added_usage_raw(dimension: JsonObject, captured_at: JsonValue) -> int:
    """Derive one dimension's raw added usage from primitive workload operands."""
    operands = _object(
        dimension.get("projection_operands"),
        PROJECTION_OPERAND_FIELDS,
        "dimension",
    )
    traffic = _object(operands["traffic"], TRAFFIC_FIELDS, "traffic")
    units_per_page = _uint(traffic["units_per_page_request"], "units_per_page_request")
    if units_per_page <= 0:
        raise GateHoldError("projection traffic fan-out is required")
    page_units = page_bound_for_window(
        capture_at=parse_time(captured_at, "captured_at"),
        window_start=parse_time(
            dimension.get("window_start"),
            "dimension.window_start",
        ),
        window_end=parse_time(dimension.get("window_end"), "dimension.window_end"),
        trailing_30d_page_requests=_uint(
            traffic["trailing_30d_page_requests"],
            "trailing_30d_page_requests",
        ),
    ) * units_per_page
    attempts = 0
    workflow_records = _records(
        operands["workflow_attempts"],
        ATTEMPT_FIELDS,
        "workflow",
    )
    workflow_kinds = {str(record["kind"]) for record in workflow_records}
    _require_nonempty_set(workflow_kinds, REQUIRED_WORKFLOW_KINDS, "workflow")
    for record in workflow_records:
        if _uint(record["min_attempts"], "min_attempts") > _uint(
            record["max_attempts"], "max_attempts"
        ):
            raise GateHoldError("workflow attempt bounds are invalid")
        if _inside_window(
            record,
            dimension.get("window_start"),
            dimension.get("window_end"),
        ):
            attempts += (
                _uint(record["max_attempts"], "max_attempts")
                + _uint(
                    record["rejected_duplicate_orphan_attempts"],
                    "rejected_duplicate_orphan_attempts",
                )
            ) * _uint(record["units_per_attempt"], "units_per_attempt")
    deployments = 0
    deployment_records = _records(
        operands["deployment_attempts"],
        DEPLOYMENT_FIELDS,
        "deployment",
    )
    deployment_ops = {str(record["operation"]) for record in deployment_records}
    _require_nonempty_set(
        deployment_ops,
        REQUIRED_DEPLOYMENT_OPERATIONS,
        "deployment",
    )
    for record in deployment_records:
        if _inside_window(
            record,
            dimension.get("window_start"),
            dimension.get("window_end"),
        ):
            deployments += (
                _uint(record["max_attempts"], "max_attempts")
                + _uint(
                    record["successful_replacement_builds"],
                    "successful_replacement_builds",
                )
            ) * _uint(record["units_per_attempt"], "units_per_attempt")
    artifact_records = _records(operands["artifacts"], ARTIFACT_FIELDS, "artifact")
    artifact_categories = {str(record["category"]) for record in artifact_records}
    _require_nonempty_set(
        artifact_categories,
        REQUIRED_ARTIFACT_CATEGORIES,
        "artifact",
    )
    artifacts = sum(
        _retention_units(record, "raw_measured_bytes")
        for record in artifact_records
    )
    backup = _object(operands["encrypted_backup"], BACKUP_FIELDS, "backup")
    backup_bytes = max(
        _uint(
            backup["last_successful_encrypted_backup_bytes"],
            "last_successful_encrypted_backup_bytes",
        ),
        _uint(
            backup["current_logical_size_estimate_bytes"],
            "current_logical_size_estimate_bytes",
        ),
    )
    if backup_bytes <= 0 or _uint(backup["attempts"], "attempts") <= 0:
        raise GateHoldError("projection encrypted backup reserve is required")
    return page_units + attempts + deployments + artifacts + _retention_units(
        {**backup, "backup_bytes": backup_bytes},
        "backup_bytes",
    )
