"""Bounded canonical file and protected review-record runtime adapters."""

# ruff: noqa: D102, D107
# pyright: reportUnannotatedClassAttribute=false

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, Final, cast

from app.services.release.receipts import canonicalize

from scripts.release_evidence_contracts import ReviewRecordAccess

if TYPE_CHECKING:
    from pathlib import Path

MAX_DOCUMENT_BYTES: Final = 1_048_576
RunProcess = Callable[..., subprocess.CompletedProcess[bytes]]


class RuntimeIOError(RuntimeError):
    """Stable filesystem boundary error."""


class BoundedPathReceiptIO:
    """Canonical receipt I/O that rejects links and oversized content."""

    def __init__(self, *, max_bytes: int = MAX_DOCUMENT_BYTES) -> None:
        if max_bytes <= 0:
            msg = "receipt_size_limit_invalid"
            raise RuntimeIOError(msg)
        self._max_bytes = max_bytes

    def read(self, path: Path) -> bytes:
        if path.is_symlink():
            msg = "receipt_symlink_forbidden"
            raise RuntimeIOError(msg)
        try:
            raw = path.read_bytes()
        except OSError as error:
            msg = "receipt_read_failed"
            raise RuntimeIOError(msg) from error
        if not raw or len(raw) > self._max_bytes:
            msg = "receipt_size_invalid"
            raise RuntimeIOError(msg)
        return raw

    def write(self, path: Path, value: bytes) -> None:
        if not value or len(value) > self._max_bytes:
            msg = "receipt_size_invalid"
            raise RuntimeIOError(msg)
        if path.is_symlink():
            msg = "receipt_symlink_forbidden"
            raise RuntimeIOError(msg)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _ = path.write_bytes(value)
        except OSError as error:
            msg = "receipt_write_failed"
            raise RuntimeIOError(msg) from error


def load_canonical_object(
    path: Path,
    *,
    io: BoundedPathReceiptIO | None = None,
    trailing_newline: bool = False,
) -> dict[str, object]:
    """Load a duplicate-key-free canonical JSON object."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                msg = "document_duplicate_key"
                raise RuntimeIOError(msg)
            result[key] = value
        return result

    raw = (io or BoundedPathReceiptIO()).read(path)
    try:
        value = cast(
            "object",
            json.loads(raw, object_pairs_hook=unique),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        msg = "document_json_invalid"
        raise RuntimeIOError(msg) from error
    if not isinstance(value, dict):
        msg = "document_object_required"
        raise RuntimeIOError(msg)
    canonical = canonicalize(cast("dict[str, object]", value))
    if raw != canonical and (not trailing_newline or raw != canonical + b"\n"):
        msg = "document_not_canonical"
        raise RuntimeIOError(msg)
    return cast("dict[str, object]", value)


class GitStatReviewAdapter:
    """Derive committed/link/permission facts without trusting YAML content."""

    def __init__(
        self,
        repository_root: Path,
        *,
        run_process: RunProcess = subprocess.run,
    ) -> None:
        self._root = repository_root.resolve(strict=True)
        self._run_process = run_process

    def inspect(self, path: Path) -> ReviewRecordAccess:
        resolved_parent = path.parent.resolve(strict=True)
        try:
            relative = resolved_parent.joinpath(path.name).relative_to(self._root)
            status = os.lstat(path)
        except (OSError, ValueError) as error:
            msg = "review_record_path_invalid"
            raise RuntimeIOError(msg) from error
        symlinked = bool(stat.S_ISLNK(status.st_mode))
        try:
            completed = self._run_process(
                ("git", "ls-files", "--error-unmatch", "--", relative.as_posix()),
                cwd=self._root,
                capture_output=True,
                check=False,
                shell=False,
                timeout=10.0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            msg = "review_record_git_probe_failed"
            raise RuntimeIOError(msg) from error
        return ReviewRecordAccess(
            committed=completed.returncode == 0,
            symlinked=symlinked,
            world_readable=(
                False if os.name == "nt" else bool(status.st_mode & stat.S_IROTH)
            ),
        )


__all__ = (
    "BoundedPathReceiptIO",
    "GitStatReviewAdapter",
    "RuntimeIOError",
    "load_canonical_object",
)
