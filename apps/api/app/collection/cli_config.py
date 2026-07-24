"""Validated collector CLI environment and bounded value factories."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from .collector_workflow import CommandSecrets

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.domain.types import JsonValue


class CliError(RuntimeError):
    """Stable fail-closed CLI error without response bodies or credentials."""


def required(environment: Mapping[str, str], name: str) -> str:
    """Read one mandatory non-blank environment value."""
    value = environment.get(name)
    if value is None or not value.strip():
        message = f"missing_environment:{name}"
        raise CliError(message)
    return value


def source_ids(environment: Mapping[str, str]) -> tuple[UUID, ...]:
    """Parse the exact nonempty unique source identity set."""
    raw = required(environment, "MONITOR_SOURCE_IDS")
    try:
        values = tuple(UUID(value.strip()) for value in raw.split(",") if value.strip())
    except ValueError as error:
        error_code = "monitor_source_ids_invalid"
        raise CliError(error_code) from error
    if not values or len(values) != len(set(values)):
        error_code = "monitor_source_ids_invalid"
        raise CliError(error_code)
    return values


def utc_datetime(value: str) -> datetime:
    """Parse an exact UTC deployment activation timestamp."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        error_code = "deployment_activation_invalid"
        raise CliError(error_code) from error
    if parsed.utcoffset() != timedelta(0):
        error_code = "deployment_activation_invalid"
        raise CliError(error_code)
    return parsed


def positive_int(environment: Mapping[str, str], name: str) -> int:
    """Parse one mandatory positive integer environment value."""
    try:
        value = int(required(environment, name))
    except ValueError as error:
        error_code = f"invalid_environment:{name}"
        raise CliError(error_code) from error
    if value < 1:
        error_code = f"invalid_environment:{name}"
        raise CliError(error_code)
    return value


def optional_uuid(value: str | None) -> UUID | None:
    """Parse an optional command identity without accepting malformed values."""
    if value is None or not value.strip():
        return None
    try:
        return UUID(value)
    except ValueError as error:
        error_code = "monitor_command_id_invalid"
        raise CliError(error_code) from error


def command_secrets() -> CommandSecrets:
    """Generate fresh one-use reservation, lease, and completion secrets."""
    return CommandSecrets(token_urlsafe(32), token_urlsafe(32), uuid4())


def system_clock() -> datetime:
    """Return the current UTC workflow clock."""
    return datetime.now(UTC)


def json_bytes(value: JsonValue) -> bytes:
    """Serialize one JSON-safe API payload compactly."""
    return json.dumps(value, separators=(",", ":")).encode()


__all__ = (
    "CliError",
    "command_secrets",
    "json_bytes",
    "optional_uuid",
    "positive_int",
    "required",
    "source_ids",
    "system_clock",
    "utc_datetime",
)
