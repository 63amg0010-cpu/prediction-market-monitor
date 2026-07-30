"""Typed handlers for the Todo 11 local and final static release gates."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from scripts.release_static_gates_evidence import (
    binding_findings,
    bounded_input,
    display,
    evidence_completeness,
    file_sha,
    plan_checklist,
    production_result_findings,
    required_range,
)
from scripts.release_static_gates_models import (
    Finding,
    GateResult,
    write_json,
    write_markdown,
)
from scripts.release_static_gates_placeholders import scan_placeholders
from scripts.release_static_gates_repo import (
    changed_paths,
    inspect_links,
    repository_root,
)
from scripts.release_static_gates_scans import (
    scan_code_quality,
    scan_scope,
    scan_secrets,
)


@dataclass(frozen=True)
class SecretScanRequest:
    """Inputs for the changed-file secret scanner."""

    root: Path
    base_sha: str
    reviewed_sha: str
    json_out: Path


@dataclass(frozen=True)
class CodeQualityRequest:
    """Inputs for the final changed-code review."""

    root: Path
    base_sha: str
    reviewed_sha: str
    evidence_dir: Path
    output: Path


@dataclass(frozen=True)
class PlanComplianceRequest:
    """Inputs for local or final plan compliance."""

    root: Path
    json_out: Path | None = None
    plan: Path | None = None
    base_sha: str | None = None
    reviewed_sha: str | None = None
    evidence_dir: Path | None = None
    production_result: Path | None = None
    expected_revision: str | None = None
    output: Path | None = None


@dataclass(frozen=True)
class ScopeFidelityRequest:
    """Inputs for local, day-zero, or acceptance scope review."""

    root: Path
    json_out: Path | None = None
    plan: Path | None = None
    base_sha: str | None = None
    reviewed_sha: str | None = None
    evidence_dir: Path | None = None
    production_result: Path | None = None
    fan_in: Path | None = None
    cadence: Path | None = None
    acceptance_refresh: Path | None = None
    expected_sha: str | None = None
    expected_plan_sha256: str | None = None
    activation_nonce: str | None = None
    predecessor_receipt: Path | None = None
    output: Path | None = None


@dataclass(frozen=True)
class LinksRequest:
    """Inputs for offline Markdown link validation."""

    root: Path
    paths: tuple[str, ...]
    json_out: Path


def run_secret_static_scan(request: SecretScanRequest) -> int:
    """Write a redacted scan receipt and return 0 or HOLD=2."""
    root = repository_root(request.root, require_git=True)
    paths = changed_paths(root, request.base_sha, request.reviewed_sha)
    result = GateResult(
        command="secret-static-scan",
        findings=scan_secrets(root, paths),
        changed_paths=paths,
        reviewed_sha=request.reviewed_sha,
    )
    write_json(request.json_out, result)
    return result.exit_code


def run_code_quality(request: CodeQualityRequest) -> int:
    """Review only changed typed source and write canonical Markdown."""
    root = repository_root(request.root, require_git=True)
    evidence = request.evidence_dir.resolve(strict=True)
    if not evidence.is_dir():
        msg = "evidence directory is not a directory"
        raise ValueError(msg)
    paths = changed_paths(root, request.base_sha, request.reviewed_sha)
    findings = (*scan_secrets(root, paths), *scan_code_quality(root, paths))
    result = GateResult(
        command="code-quality",
        findings=tuple(findings),
        changed_paths=paths,
        reviewed_sha=request.reviewed_sha,
    )
    write_markdown(request.output, result)
    return result.exit_code


def run_plan_compliance(request: PlanComplianceRequest) -> int:
    """Run the local placeholder gate or the final evidence audit."""
    root = repository_root(request.root)
    findings = list(scan_placeholders(root))
    paths: tuple[str, ...] = ()
    plan_sha: str | None = None
    evidence_count = 0
    if request.base_sha is not None or request.reviewed_sha is not None:
        base, reviewed = required_range(request.base_sha, request.reviewed_sha)
        paths = changed_paths(root, base, reviewed)
    if request.plan is not None:
        plan = bounded_input(root, request.plan)
        plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
        findings.extend(plan_checklist(plan))
    if request.evidence_dir is not None:
        evidence = request.evidence_dir.resolve(strict=True)
        evidence_findings, evidence_count = evidence_completeness(evidence)
        findings.extend(evidence_findings)
    if request.production_result is not None:
        findings.extend(
            production_result_findings(
                request.production_result,
                request.reviewed_sha,
                plan_sha,
                request.expected_revision,
            )
        )
    result = GateResult(
        command="plan-compliance",
        findings=tuple(findings),
        changed_paths=paths,
        reviewed_sha=request.reviewed_sha,
        plan_sha256=plan_sha,
        evidence_count=evidence_count,
    )
    output = request.output or request.json_out
    if output is None:
        msg = "plan-compliance requires an output"
        raise ValueError(msg)
    if request.output is not None:
        write_markdown(output, result)
    else:
        write_json(output, result)
    return result.exit_code


def run_scope_fidelity(request: ScopeFidelityRequest) -> int:
    """Reject repository and receipt drift from the reviewed scope."""
    root = repository_root(request.root)
    paths: tuple[str, ...] = ()
    findings: list[Finding] = []
    if request.base_sha is not None or request.reviewed_sha is not None:
        base, reviewed = required_range(request.base_sha, request.reviewed_sha)
        paths = changed_paths(root, base, reviewed)
        findings.extend(scan_scope(root, paths))
    plan_sha: str | None = None
    if request.plan is not None:
        plan = bounded_input(root, request.plan)
        plan_sha = hashlib.sha256(plan.read_bytes()).hexdigest()
        if (
            request.expected_plan_sha256 is not None
            and request.expected_plan_sha256 != plan_sha
        ):
            findings.append(Finding("plan_sha_mismatch", display(root, plan)))
    for label, path in _scope_receipts(request):
        findings.extend(
            binding_findings(
                label,
                path,
                request.expected_sha or request.reviewed_sha,
                request.expected_plan_sha256 or plan_sha,
                request.activation_nonce,
            )
        )
    if (
        request.predecessor_receipt is not None
        and request.cadence is not None
        and file_sha(request.predecessor_receipt) != file_sha(request.cadence)
    ):
        findings.append(Finding("predecessor_not_cadence", "predecessor-receipt"))
    result = GateResult(
        command="scope-fidelity",
        findings=tuple(findings),
        changed_paths=paths,
        reviewed_sha=request.reviewed_sha or request.expected_sha,
        plan_sha256=plan_sha,
    )
    output = request.output or request.json_out
    if output is None:
        msg = "scope-fidelity requires an output"
        raise ValueError(msg)
    if request.output is not None:
        write_markdown(output, result)
    else:
        write_json(output, result)
    return result.exit_code


def run_links(request: LinksRequest) -> int:
    """Validate Markdown links locally and never contact external hosts."""
    root = repository_root(request.root)
    findings, checked = inspect_links(root, request.paths)
    result = GateResult(
        command="links",
        findings=findings,
        checked_links=checked,
    )
    write_json(request.json_out, result)
    return result.exit_code


def _scope_receipts(request: ScopeFidelityRequest) -> tuple[tuple[str, Path], ...]:
    values = (
        ("production_result", request.production_result),
        ("fan_in", request.fan_in),
        ("cadence", request.cadence),
        ("acceptance_refresh", request.acceptance_refresh),
    )
    return tuple((label, path) for label, path in values if path is not None)
