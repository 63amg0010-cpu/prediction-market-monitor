"""Injected, redacted acceptance current-state evidence materialization."""

# ruff: noqa: EM101, EM102, PLR2004, TC003

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from app.services.release.receipts import canonicalize

from .release_chain_acceptance import (
    CURRENT_NAMES,
    INPUT_NAMES,
    NamedPath,
    validate_fresh,
)
from .release_chain_common import (
    Bindings,
    Clock,
    JsonObject,
    JsonValue,
    ReceiptIO,
    ReleaseChainError,
    bindings_of,
    build_receipt,
    digest,
    load_document,
    require_bindings,
    verified_receipt,
    write_receipt,
)


@dataclass(frozen=True, slots=True)
class CaptureObservation:
    """Public-safe result returned by an injected capture probe."""

    evidence_sha256: str
    captured_at: datetime
    tool_version: str
    accepted: bool


class CurrentCaptureProvider(Protocol):
    """External inspection boundary; tests provide a no-network implementation."""

    def capture(self, member_name: str) -> CaptureObservation:
        """Return one already-redacted capture observation."""
        ...


@dataclass(frozen=True, slots=True)
class AcceptanceCaptureRequest:
    """Typed parser input for ``acceptance-capture``."""

    inputs: tuple[NamedPath, ...]
    input_manifest: Path
    free_tier_result: Path
    output_dir: Path
    current_state_out: Path
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: Path


def handle_acceptance_capture(
    request: AcceptanceCaptureRequest,
    *,
    io: ReceiptIO,
    clock: Clock,
    provider: CurrentCaptureProvider,
) -> JsonObject:
    """Materialize six injected inspections and an exact 15-member manifest."""
    predecessor = verified_receipt(io, request.predecessor_receipt)
    input_manifest = verified_receipt(io, request.input_manifest)
    free_tier = verified_receipt(io, request.free_tier_result)
    expected = bindings_of(predecessor)
    _request_bindings(request, expected)
    require_bindings(input_manifest, expected)
    require_bindings(free_tier, expected)
    if free_tier["predecessor_receipt_sha256"] != input_manifest["receipt_sha256"]:
        raise ReleaseChainError("free_tier_not_input_successor")
    input_paths = _inputs(request.inputs)
    input_hashes = _input_manifest_hashes(input_manifest)
    members: list[JsonValue] = []
    for name in INPUT_NAMES:
        raw = io.read(input_paths[name])
        _ = load_document(io, input_paths[name], allow_trailing_newline=True)
        member_sha = digest(raw)
        if input_hashes.get(name) != member_sha:
            raise ReleaseChainError(f"input_member_changed:{name}")
        members.append({"name": name, "sha256": member_sha})
    free_tier_raw = io.read(request.free_tier_result)
    members.append({"name": "free-tier-result.json", "sha256": digest(free_tier_raw)})
    captured_now = clock()
    for name in CURRENT_NAMES:
        observation = provider.capture(name)
        validate_fresh(observation.captured_at, captured_now)
        if not observation.accepted or not _sha256(observation.evidence_sha256):
            raise ReleaseChainError(f"current_capture_rejected:{name}")
        body: JsonObject = {
            "schema": "release-current-member.v1",
            "member": name,
            "reviewed_sha": expected.reviewed_sha,
            "approved_plan_sha256": expected.approved_plan_sha256,
            "activation_nonce": expected.activation_nonce,
            "accepted": True,
            "captured_at": observation.captured_at.isoformat(),
            "evidence_sha256": observation.evidence_sha256,
            "tool_version": observation.tool_version,
        }
        raw = canonicalize(body)
        io.write(request.output_dir / name, raw)
        members.append({"name": name, "sha256": digest(raw)})
    receipt = build_receipt(
        command="acceptance-capture",
        predecessor=predecessor,
        clock=lambda: captured_now,
        details={
            "input_manifest_sha256": input_manifest["receipt_sha256"],
            "member_count": 15,
            "members": members,
        },
    )
    write_receipt(io, request.current_state_out, receipt)
    return receipt


def _inputs(members: tuple[NamedPath, ...]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for member in members:
        if member.name in result:
            raise ReleaseChainError("duplicate_input_member")
        result[member.name] = member.path
    if set(result) != set(INPUT_NAMES):
        raise ReleaseChainError("capture_inputs_not_exact")
    return result


def _input_manifest_hashes(receipt: JsonObject) -> dict[str, str]:
    details = receipt.get("details")
    if not isinstance(details, dict) or details.get("member_count") != 8:
        raise ReleaseChainError("input_manifest_invalid")
    members = details.get("members")
    if not isinstance(members, list) or len(members) != 8:
        raise ReleaseChainError("input_manifest_invalid")
    result: dict[str, str] = {}
    for value in members:
        if not isinstance(value, dict):
            raise ReleaseChainError("input_manifest_member_invalid")
        name, member_sha = value.get("name"), value.get("sha256")
        if (
            not isinstance(name, str)
            or not isinstance(member_sha, str)
            or name in result
        ):
            raise ReleaseChainError("input_manifest_member_invalid")
        result[name] = member_sha
    if set(result) != set(INPUT_NAMES):
        raise ReleaseChainError("input_manifest_members_not_exact")
    return result


def _request_bindings(
    request: AcceptanceCaptureRequest,
    expected: Bindings,
) -> None:
    if (
        request.expected_sha != expected.reviewed_sha
        or request.expected_plan_sha256 != expected.approved_plan_sha256
        or request.activation_nonce != expected.activation_nonce
    ):
        raise ReleaseChainError("caller_binding_mismatch")


def _sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
