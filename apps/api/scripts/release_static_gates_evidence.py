"""Schema-closed evidence validation for Todo 11 final reports."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Final, cast

if TYPE_CHECKING:
    from pathlib import Path

from scripts.release_static_gates_models import Finding, canonical_bytes

RECEIPT_SHA = re.compile(r"[0-9a-f]{64}")
REVISION_KEYS: Final = ("revision", "current_revision", "expected_revision")


def required_range(
    base_sha: str | None,
    reviewed_sha: str | None,
) -> tuple[str, str]:
    """Require the two operands of an immutable Git range together."""
    if base_sha is None or reviewed_sha is None:
        msg = "base and reviewed SHA must be supplied together"
        raise ValueError(msg)
    return base_sha, reviewed_sha


def bounded_input(root: Path, path: Path) -> Path:
    """Require a regular input below the repository root."""
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(root):
        msg = "input must be a repository file"
        raise ValueError(msg)
    return resolved


def plan_checklist(plan: Path) -> list[Finding]:
    """Require all numbered implementation Todos in the approved plan."""
    text = plan.read_text(encoding="utf-8")
    return [
        Finding("missing_plan_todo", plan.name, number)
        for number in range(1, 13)
        if re.search(rf"(?m)^- \[[ xX]\] {number}\.", text) is None
    ]


def evidence_completeness(evidence: Path) -> tuple[list[Finding], int]:
    """Require one complete canonical evidence envelope per Todo."""
    findings: list[Finding] = []
    accepted = 0
    for number in range(1, 13):
        candidates = sorted(evidence.glob(f"task-{number}-*/result.json"))
        if any(_complete_evidence_receipt(path) for path in candidates):
            accepted += 1
        else:
            findings.append(Finding("missing_todo_evidence", f"task-{number}"))
    return findings, accepted


def production_result_findings(
    path: Path,
    reviewed_sha: str | None,
    plan_sha: str | None,
    expected_revision: str | None,
) -> list[Finding]:
    """Validate the immutable Todo 12 result bindings."""
    document = _json_object(path)
    findings: list[Finding] = []
    if document.get("accepted") is not True or document.get("redacted") is not True:
        findings.append(Finding("production_result_not_accepted", path.name))
    if reviewed_sha is not None and document.get("reviewed_sha") != reviewed_sha:
        findings.append(Finding("production_reviewed_sha_mismatch", path.name))
    if plan_sha is not None and document.get("approved_plan_sha256") != plan_sha:
        findings.append(Finding("production_plan_sha_mismatch", path.name))
    revisions = {document.get(key) for key in REVISION_KEYS}
    if expected_revision is not None and expected_revision not in revisions:
        findings.append(Finding("production_revision_mismatch", path.name))
    if not _valid_receipt_hash(document):
        findings.append(Finding("invalid_receipt_sha", path.name))
    return findings


def binding_findings(
    label: str,
    path: Path,
    reviewed_sha: str | None,
    plan_sha: str | None,
    activation_nonce: str | None,
) -> list[Finding]:
    """Validate common scope receipt bindings without exposing values."""
    document = _json_object(path)
    expected = (
        ("reviewed_sha", reviewed_sha),
        ("approved_plan_sha256", plan_sha),
        ("activation_nonce", activation_nonce),
    )
    findings = [
        Finding(f"{label}_{key}_mismatch", label)
        for key, value in expected
        if value is not None and document.get(key) != value
    ]
    if document.get("accepted") is not True:
        findings.append(Finding(f"{label}_not_accepted", label))
    return findings


def file_sha(path: Path) -> str:
    """Hash one exact receipt's bytes."""
    return hashlib.sha256(path.resolve(strict=True).read_bytes()).hexdigest()


def display(root: Path, path: Path) -> str:
    """Return a stable repository-relative path."""
    return path.relative_to(root).as_posix()


def _complete_evidence_receipt(path: Path) -> bool:
    try:
        document = _json_object(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    assertions = document.get("assertions")
    assertion_fields = (
        cast("dict[object, object]", assertions)
        if isinstance(assertions, dict)
        else {}
    )
    return (
        document.get("exit_code") == 0
        and document.get("redacted") is True
        and bool(assertion_fields)
        and _valid_receipt_hash(document)
    )


def _json_object(path: Path) -> dict[str, object]:
    raw = cast("object", json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        msg = "receipt must be a JSON object"
        raise TypeError(msg)
    mapping = cast("dict[object, object]", raw)
    if not all(isinstance(key, str) for key in mapping):
        msg = "receipt keys must be strings"
        raise ValueError(msg)
    return {str(key): value for key, value in mapping.items()}


def _valid_receipt_hash(document: dict[str, object]) -> bool:
    claimed = document.get("receipt_sha256")
    if not isinstance(claimed, str) or RECEIPT_SHA.fullmatch(claimed) is None:
        return False
    body = {key: value for key, value in document.items() if key != "receipt_sha256"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest() == claimed
