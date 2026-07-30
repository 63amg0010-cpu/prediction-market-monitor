"""Bounded parsing and static scans for the privacy provider adapter."""

# ruff: noqa: EM101, TC002, TC003

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast
from urllib.parse import urlsplit

from app.domain.types import JsonValue

from scripts.runtime_privacy_adapter import PrivacyRuntimeError, digest

MAX_SCAN_BYTES: Final = 16_000_000
_EXCLUDED_PARTS: Final = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tmp",
        ".venv",
        "__pycache__",
        "node_modules",
    }
)
_TEXT_SUFFIXES: Final = frozenset(
    {
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".py",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
    }
)
_FORBIDDEN = re.compile(
    b"".join(
        (
            rb"(?i)(postgres(?:ql)?://|authorization:\s*bearer|",
            rb'"(?:author|profile|address|database_url|secret|token)"\s*:)',
        )
    )
)


def https_base(value: str) -> str:
    """Validate one public HTTPS base without credentials or redirects."""
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PrivacyRuntimeError("provider_url_invalid")
    return value.rstrip("/")


def json_object(raw: bytes) -> dict[str, JsonValue]:
    """Parse one bounded HTTP response object."""
    try:
        loaded = cast("object", json.loads(raw))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise PrivacyRuntimeError("provider_response_invalid") from error
    if not isinstance(loaded, dict):
        raise PrivacyRuntimeError("provider_response_invalid")
    return cast("dict[str, JsonValue]", loaded)


def zero_content(value: Mapping[str, JsonValue]) -> bool:
    """Accept an explicit zero count or empty result collection."""
    for key in ("count", "total", "total_count"):
        if key in value:
            return value[key] == 0
    for key in ("items", "posts", "results", "data"):
        item = value.get(key)
        if isinstance(item, list):
            return len(item) == 0
    return False


def health_ok(value: Mapping[str, JsonValue]) -> tuple[bool, bool]:
    """Require restored health plus a disabled, zero-result provider."""
    healthy = value.get("status") == "ok" and value.get("db") == "ok"
    manifold_zero = value.get("manifold_enabled") is False
    results = value.get("manifold_results")
    if isinstance(results, int):
        manifold_zero &= results == 0
    return healthy, manifold_zero


def clean(raw: bytes, protected: tuple[bytes, ...]) -> bool:
    """Reject protected identities and forbidden structured fields."""
    lowered = raw.lower()
    return not _FORBIDDEN.search(raw) and not any(
        value and value.lower() in lowered for value in protected
    )


def repository_scan(
    root: Path,
    protected: tuple[bytes, ...],
) -> tuple[bool, tuple[str, ...]]:
    """Scan bounded text files and retain only their one-way hashes."""
    hashes: list[str] = []
    passed = True
    total = 0
    for path in sorted(root.rglob("*")):
        if (
            not path.is_file()
            or _EXCLUDED_PARTS.intersection(path.parts)
            or path.suffix.lower() not in _TEXT_SUFFIXES
        ):
            continue
        size = path.stat().st_size
        total += size
        if total > MAX_SCAN_BYTES:
            raise PrivacyRuntimeError("repository_scan_too_large")
        raw = path.read_bytes()
        hashes.append(digest(raw.hex()))
        passed &= clean(raw, protected)
    return passed, tuple(hashes)


__all__ = (
    "clean",
    "health_ok",
    "https_base",
    "json_object",
    "repository_scan",
    "zero_content",
)
