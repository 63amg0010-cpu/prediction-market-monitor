"""Typed, deterministic output models for Todo 11 static release gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import IntEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from pathlib import Path

SCHEMA: Final = "fresh-search.static-gate.v1"


class GateExit(IntEnum):
    """Stable process semantics shared by every static gate."""

    ACCEPTED = 0
    HOLD = 2


@dataclass(frozen=True, order=True)
class Finding:
    """One public-safe finding without source content."""

    code: str
    path: str
    line: int = 0

    def as_dict(self) -> dict[str, str | int]:
        """Return a canonical JSON projection."""
        item: dict[str, str | int] = {"code": self.code, "path": self.path}
        if self.line > 0:
            item["line"] = self.line
        return item


@dataclass(frozen=True)
class GateResult:
    """One complete, redacted static-gate decision."""

    command: str
    findings: tuple[Finding, ...]
    changed_paths: tuple[str, ...] = ()
    checked_links: int = 0
    reviewed_sha: str | None = None
    plan_sha256: str | None = None
    evidence_count: int = 0

    @property
    def accepted(self) -> bool:
        """Return whether the gate has no finding."""
        return not self.findings

    @property
    def exit_code(self) -> int:
        """Return the exact CLI exit code."""
        return int(GateExit.ACCEPTED if self.accepted else GateExit.HOLD)

    def document(self) -> dict[str, object]:
        """Build a content-addressed, canonical, public-safe document."""
        body: dict[str, object] = {
            "accepted": self.accepted,
            "changed_paths": list(self.changed_paths),
            "command": self.command,
            "findings": [item.as_dict() for item in sorted(set(self.findings))],
            "redacted": True,
            "schema": SCHEMA,
        }
        if self.checked_links:
            body["checked_links"] = self.checked_links
        if self.reviewed_sha is not None:
            body["reviewed_sha"] = self.reviewed_sha
        if self.plan_sha256 is not None:
            body["plan_sha256"] = self.plan_sha256
        if self.evidence_count:
            body["evidence_count"] = self.evidence_count
        digest = hashlib.sha256(canonical_bytes(body)).hexdigest()
        return {**body, "receipt_sha256": digest}


def canonical_bytes(document: object) -> bytes:
    """Serialize the supported JSON values deterministically."""
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def write_json(path: Path, result: GateResult) -> None:
    """Write one newline-terminated canonical JSON report."""
    _prepare_output(path)
    _ = path.write_bytes(canonical_bytes(result.document()) + b"\n")


def write_markdown(path: Path, result: GateResult) -> None:
    """Write one deterministic redacted reviewer report."""
    _prepare_output(path)
    lines = ["APPROVE" if result.accepted else "REJECT", ""]
    lines.extend(
        (
            f"- command: `{result.command}`",
            f"- reviewed_sha: `{result.reviewed_sha or 'not-applicable'}`",
            f"- plan_sha256: `{result.plan_sha256 or 'not-applicable'}`",
            f"- changed_paths: {len(result.changed_paths)}",
            f"- evidence_receipts: {result.evidence_count}",
            "- redacted: `true`",
            "",
            "## Findings",
            "",
        )
    )
    if result.findings:
        for item in sorted(set(result.findings)):
            location = f":{item.line}" if item.line else ""
            lines.append(f"- `{item.code}` — `{item.path}{location}`")
    else:
        lines.append("- none")
    _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _prepare_output(path: Path) -> None:
    if path.exists() and path.is_symlink():
        msg = "output path must not be a symlink"
        raise ValueError(msg)
    path.parent.mkdir(parents=True, exist_ok=True)
