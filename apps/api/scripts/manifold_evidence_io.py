"""Small file and argument boundaries for the Manifold evidence CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.configuration.manifold_evidence import (
    JsonDocument,
    canonical_bytes,
)

if TYPE_CHECKING:
    from pathlib import Path


def write(path: Path, value: JsonDocument) -> None:
    """Write one canonical JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(canonical_bytes(value) + b"\n")


def required(value: str | None, name: str) -> str:
    """Return a required CLI value or fail closed."""
    if value is None:
        message = f"{name} is required"
        raise RuntimeError(message)
    return value
