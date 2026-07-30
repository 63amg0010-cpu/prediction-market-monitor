"""Bounded repository and Markdown inspection for Todo 11 gates."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import unquote, urlsplit

import anyio
from anyio.to_thread import run_sync as run_sync_in_worker_thread

from scripts.release_static_gates_models import Finding

MAX_SCAN_BYTES = 1_048_576
MARKDOWN_LINK = re.compile(r"!?\[[^\]]*]\((?P<target>[^)\n]+)\)")
MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*#*\s*$")
EXCLUDED_PARTS = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
)


def repository_root(root: Path, *, require_git: bool = False) -> Path:
    """Resolve a bounded directory and optionally require Git metadata."""
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        msg = "root is not a directory"
        raise ValueError(msg)
    if require_git and not (resolved / ".git").exists():
        msg = "root is not a Git repository"
        raise ValueError(msg)
    return resolved


async def _git_process(root: Path, arguments: tuple[str, ...]) -> bytes:
    environment = os.environ.copy()
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    completed = await anyio.run_process(
        ("git", *arguments),
        cwd=root,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        msg = "Git range validation failed"
        raise ValueError(msg)
    return completed.stdout


def _process_loop_factory() -> asyncio.AbstractEventLoop:
    loop_type = cast(
        "type[asyncio.AbstractEventLoop]",
        getattr(asyncio, "ProactorEventLoop", asyncio.SelectorEventLoop),
    )
    return loop_type()


def _git_in_process_loop(root: Path, arguments: tuple[str, ...]) -> bytes:
    runner = asyncio.Runner(loop_factory=_process_loop_factory)
    with runner:
        local_loop = runner.get_loop()
        result = runner.run(_git_process(root, arguments))
    _require_closed(local_loop)
    return result


async def _git_in_worker(root: Path, arguments: tuple[str, ...]) -> bytes:
    return await run_sync_in_worker_thread(_git_in_process_loop, root, arguments)


def _git_in_local_selector(root: Path, arguments: tuple[str, ...]) -> bytes:
    runner = asyncio.Runner(loop_factory=asyncio.SelectorEventLoop)
    with runner:
        local_loop = runner.get_loop()
        result = runner.run(_git_in_worker(root, arguments))
    _require_closed(local_loop)
    return result


def _require_closed(loop: asyncio.AbstractEventLoop) -> None:
    if not loop.is_closed():
        msg = "local event loop did not close"
        raise RuntimeError(msg)


def changed_paths(root: Path, base_sha: str, reviewed_sha: str) -> tuple[str, ...]:
    """Return canonical changed paths for an exact commit range."""
    actual_root = repository_root(root, require_git=True)
    for value in (base_sha, reviewed_sha):
        if re.fullmatch(r"[0-9a-f]{40}", value) is None:
            msg = "commit SHA must be 40 lowercase hexadecimal characters"
            raise ValueError(msg)
        resolved = _git_in_local_selector(
            actual_root,
            ("rev-parse", f"{value}^{{commit}}"),
        )
        if resolved.decode().strip() != value:
            msg = "commit SHA does not resolve exactly"
            raise ValueError(msg)
    raw = _git_in_local_selector(
        actual_root,
        (
            "diff",
            "--name-only",
            "--diff-filter=ACMR",
            "-z",
            f"{base_sha}..{reviewed_sha}",
            "--",
        ),
    )
    names = sorted({item for item in raw.decode().split("\0") if item})
    for name in names:
        _bounded_relative(name)
    return tuple(names)


def bounded_path(root: Path, relative: str) -> Path:
    """Resolve one repository-relative path without traversal."""
    _bounded_relative(relative)
    target = (root / Path(relative)).resolve(strict=True)
    if not target.is_relative_to(root):
        msg = "path escapes repository root"
        raise ValueError(msg)
    return target


def read_changed_file(root: Path, relative: str) -> bytes:
    """Read one bounded changed file, rejecting oversized inputs."""
    target = bounded_path(root, relative)
    if not target.is_file():
        msg = "changed path is not a regular file"
        raise ValueError(msg)
    if target.stat().st_size > MAX_SCAN_BYTES:
        msg = "changed file exceeds scan limit"
        raise ValueError(msg)
    return target.read_bytes()


def walk_files(root: Path, paths: tuple[str, ...]) -> tuple[tuple[str, Path], ...]:
    """Expand bounded file/directory operands in stable order."""
    selected: dict[str, Path] = {}
    for relative in paths:
        if not (root / relative).exists():
            continue
        target = bounded_path(root, relative)
        candidates = (target,) if target.is_file() else target.rglob("*")
        for candidate in candidates:
            if not candidate.is_file() or EXCLUDED_PARTS.intersection(candidate.parts):
                continue
            canonical = candidate.relative_to(root).as_posix()
            selected[canonical] = candidate
    return tuple(sorted(selected.items()))


def inspect_links(
    root: Path,
    paths: tuple[str, ...],
) -> tuple[tuple[Finding, ...], int]:
    """Validate local Markdown targets/anchors without network access."""
    findings: list[Finding] = []
    count = 0
    for relative, path in walk_files(root, paths):
        if path.suffix.lower() not in {".md", ".markdown"}:
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in MARKDOWN_LINK.finditer(line):
                count += 1
                target = match.group("target").strip().strip("<>")
                code = _link_error(root, path, target)
                if code is not None:
                    findings.append(Finding(code, relative, line_number))
    return tuple(findings), count


def _link_error(root: Path, source: Path, raw_target: str) -> str | None:
    target = raw_target.split(maxsplit=1)[0]
    parsed = urlsplit(target)
    if parsed.scheme:
        safe_http = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1",
            "localhost",
        }
        return (
            None
            if parsed.scheme in {"https", "mailto"} or safe_http
            else "unsafe_link_scheme"
        )
    decoded_path = unquote(parsed.path)
    destination = source if not decoded_path else source.parent / decoded_path
    try:
        resolved = destination.resolve(strict=True)
    except OSError:
        return "missing_link_target"
    if not resolved.is_relative_to(root):
        return "link_target_outside_root"
    if parsed.fragment and resolved.suffix.lower() in {".md", ".markdown"}:
        headings = _headings(resolved)
        if unquote(parsed.fragment).lower() not in headings:
            return "missing_link_anchor"
    return None


def _headings(path: Path) -> set[str]:
    headings: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = MARKDOWN_HEADING.match(line)
        if match is not None:
            slug = re.sub(r"[^\w\s-]", "", match.group("title").lower())
            headings.add(re.sub(r"[\s-]+", "-", slug).strip("-"))
    return headings


def _bounded_relative(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or not value or ".." in path.parts or "\\" in value:
        msg = "repository path must be canonical and relative"
        raise ValueError(msg)
