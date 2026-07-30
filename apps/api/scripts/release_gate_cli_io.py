"""Redacted JSON helpers for release-gate adapters."""

# ruff: noqa: PLR2004

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import JsonValue, TypeAdapter, ValidationError

type JsonObject = dict[str, JsonValue]

_DOCUMENT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)


def read_document(value: str) -> JsonObject:
    """Read one schema-root JSON object without logging its contents."""
    try:
        return _DOCUMENT.validate_json(Path(value).read_bytes())
    except (OSError, ValidationError) as error:
        message = "release_gate_json_input_rejected"
        raise ValueError(message) from error


def read_bytes(value: str) -> bytes:
    """Read bounded command input bytes."""
    try:
        raw = Path(value).read_bytes()
    except OSError as error:
        message = "release_gate_input_unreadable"
        raise ValueError(message) from error
    if len(raw) > 262_144:
        message = "release_gate_input_too_large"
        raise ValueError(message)
    return raw


def write_document(value: str, document: JsonObject) -> None:
    """Write one public-safe JSON receipt atomically enough for local handoff."""
    target = Path(value)
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def csv(value: str) -> tuple[str, ...]:
    """Parse a nonempty, duplicate-free ordered comma list."""
    items = tuple(item.strip() for item in value.split(",") if item.strip())
    if not items or len(items) != len(set(items)):
        message = "release_gate_csv_rejected"
        raise ValueError(message)
    return items


def strings(value: object) -> tuple[str, ...]:
    """Narrow an argparse append value."""
    if not isinstance(value, list) or not value:
        message = "release_gate_repeated_option_rejected"
        raise ValueError(message)
    typed = cast("list[object]", value)
    if not all(isinstance(item, str) and item for item in typed):
        message = "release_gate_repeated_option_rejected"
        raise ValueError(message)
    return tuple(cast("list[str]", typed))


__all__ = (
    "JsonObject",
    "csv",
    "read_bytes",
    "read_document",
    "strings",
    "write_document",
)
