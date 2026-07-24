"""Deterministic OpenAPI serialization for generated contract artifacts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI


def write_openapi(app: FastAPI, target: Path) -> bytes:
    """Write the current FastAPI schema with stable key and newline ordering."""
    schema = app.openapi()
    rendered = (
        json.dumps(
            schema,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_bytes(rendered)
    return rendered


def write_openapi_json(app: FastAPI, target: Path) -> bytes:
    """Compatibility name for the deterministic OpenAPI writer."""
    return write_openapi(app, target)


__all__ = ["write_openapi", "write_openapi_json"]
