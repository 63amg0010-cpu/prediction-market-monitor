"""Content-addressed 30-day acceptance input and refresh handlers."""

# ruff: noqa: EM101, EM102, PLR2004, TC003

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

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

INPUT_NAMES: Final = (
    "manifold-evidence.json",
    "production-free-tier.json",
    "free-tier-measurements.json",
    "github-verified.json",
    "vercel-api-verified.json",
    "vercel-web-verified.json",
    "supabase-verified.json",
    "production-db-measurements.json",
)
CURRENT_NAMES: Final = (
    "repository-scan.json",
    "github-public-scan.json",
    "vercel-api-inspection.json",
    "vercel-web-inspection.json",
    "provider-log-disposition.json",
    "db-binding-health.json",
)


@dataclass(frozen=True, slots=True)
class NamedPath:
    """One logical manifest member and its immutable path."""

    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class AcceptanceInputManifestRequest:
    """Typed parser input for ``acceptance-input-manifest``."""

    leaves: tuple[NamedPath, ...]
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: Path
    output_root: Path
    json_out: Path


@dataclass(frozen=True, slots=True)
class AcceptanceRefreshRequest:
    """Typed parser input for immutable 15-member refresh verification."""

    members: tuple[NamedPath, ...]
    input_manifest: Path
    current_state_manifest: Path
    expected_members: int
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: Path
    json_out: Path


def handle_acceptance_input_manifest(
    request: AcceptanceInputManifestRequest,
    *,
    io: ReceiptIO,
    clock: Clock,
) -> JsonObject:
    """Byte-verify and content-address exactly eight immutable input leaves."""
    predecessor = verified_receipt(io, request.predecessor_receipt)
    expected = bindings_of(predecessor)
    _request_bindings(request, expected)
    indexed = _exact_members(request.leaves, INPUT_NAMES)
    manifest_members: list[JsonValue] = []
    content: dict[str, bytes] = {}
    for name in INPUT_NAMES:
        raw = io.read(indexed[name])
        document = load_document(io, indexed[name], allow_trailing_newline=True)
        _leaf_bindings(document, expected)
        content[name] = raw
        manifest_members.append(
            {"name": name, "sha256": digest(raw), "size_bytes": len(raw)}
        )
    receipt = build_receipt(
        command="acceptance-input-manifest",
        predecessor=predecessor,
        clock=clock,
        details={"member_count": 8, "members": manifest_members},
    )
    capture_root = request.output_root / str(receipt["receipt_sha256"])
    for name, raw in content.items():
        io.write(capture_root / name, raw)
    write_receipt(io, request.json_out, receipt)
    return receipt


def handle_acceptance_refresh(
    request: AcceptanceRefreshRequest,
    *,
    io: ReceiptIO,
    clock: Clock,
) -> JsonObject:
    """Verify exact explicit arguments against the 15-member current manifest."""
    predecessor = verified_receipt(io, request.predecessor_receipt)
    current = verified_receipt(io, request.current_state_manifest)
    input_manifest = verified_receipt(io, request.input_manifest)
    expected = bindings_of(predecessor)
    _request_bindings(request, expected)
    require_bindings(current, expected)
    require_bindings(input_manifest, expected)
    if predecessor["receipt_sha256"] != current["receipt_sha256"]:
        raise ReleaseChainError("refresh_predecessor_not_current_state")
    names = (*INPUT_NAMES, "free-tier-result.json", *CURRENT_NAMES)
    if request.expected_members != 15:
        raise ReleaseChainError("expected_members_must_be_15")
    indexed = _exact_members(request.members, names)
    declared = _declared_members(current, expected_count=15)
    actual: list[JsonValue] = []
    for name in names:
        raw = io.read(indexed[name])
        _ = load_document(io, indexed[name], allow_trailing_newline=True)
        actual_sha = digest(raw)
        if declared.get(name) != actual_sha:
            raise ReleaseChainError(f"current_member_changed:{name}")
        actual.append({"name": name, "sha256": actual_sha})
    details = _details(current)
    if details.get("input_manifest_sha256") != input_manifest["receipt_sha256"]:
        raise ReleaseChainError("input_manifest_binding_mismatch")
    receipt = build_receipt(
        command="acceptance-refresh",
        predecessor=current,
        clock=clock,
        details={
            "current_state_sha256": current["receipt_sha256"],
            "input_manifest_sha256": input_manifest["receipt_sha256"],
            "member_count": 15,
            "members": actual,
        },
    )
    write_receipt(io, request.json_out, receipt)
    return receipt


def validate_fresh(timestamp: datetime, now: datetime) -> None:
    """Require a capture to be nonfuture and strictly younger than two hours."""
    if timestamp.tzinfo is None or now.tzinfo is None:
        raise ReleaseChainError("capture_time_not_timezone_aware")
    age = now - timestamp
    if age < timedelta(0) or age >= timedelta(hours=2):
        raise ReleaseChainError("capture_not_fresh")


def _exact_members(
    members: tuple[NamedPath, ...],
    expected_names: tuple[str, ...],
) -> dict[str, Path]:
    indexed: dict[str, Path] = {}
    for member in members:
        if member.name in indexed:
            raise ReleaseChainError(f"duplicate_member:{member.name}")
        indexed[member.name] = member.path
    if set(indexed) != set(expected_names) or len(indexed) != len(expected_names):
        raise ReleaseChainError("manifest_members_not_exact")
    return indexed


def _leaf_bindings(document: JsonObject, expected: Bindings) -> None:
    present = {
        "reviewed_sha",
        "approved_plan_sha256",
        "approval_round_id",
        "approval_launch_sha256s",
        "activation_nonce",
    }.intersection(document)
    if present:
        required = {"reviewed_sha", "approved_plan_sha256", "activation_nonce"}
        if not required.issubset(document):
            raise ReleaseChainError("leaf_binding_incomplete")
        if document["reviewed_sha"] != expected.reviewed_sha:
            raise ReleaseChainError("leaf_reviewed_sha_mismatch")
        if document["approved_plan_sha256"] != expected.approved_plan_sha256:
            raise ReleaseChainError("leaf_plan_sha_mismatch")
        if document["activation_nonce"] != expected.activation_nonce:
            raise ReleaseChainError("leaf_activation_nonce_mismatch")


def _declared_members(receipt: JsonObject, *, expected_count: int) -> dict[str, str]:
    details = _details(receipt)
    if details.get("member_count") != expected_count:
        raise ReleaseChainError("current_member_count_mismatch")
    members = details.get("members")
    if not isinstance(members, list) or len(members) != expected_count:
        raise ReleaseChainError("current_members_invalid")
    indexed: dict[str, str] = {}
    for member in members:
        if not isinstance(member, dict):
            raise ReleaseChainError("current_member_invalid")
        name, member_sha = member.get("name"), member.get("sha256")
        if (
            not isinstance(name, str)
            or not isinstance(member_sha, str)
            or name in indexed
        ):
            raise ReleaseChainError("current_member_invalid")
        indexed[name] = member_sha
    return indexed


def _details(receipt: JsonObject) -> JsonObject:
    value = receipt.get("details")
    if not isinstance(value, dict):
        raise ReleaseChainError("receipt_details_invalid")
    return value


def _request_bindings(
    request: AcceptanceInputManifestRequest | AcceptanceRefreshRequest,
    expected: Bindings,
) -> None:
    if (
        request.expected_sha != expected.reviewed_sha
        or request.expected_plan_sha256 != expected.approved_plan_sha256
        or request.activation_nonce != expected.activation_nonce
    ):
        raise ReleaseChainError("caller_binding_mismatch")
