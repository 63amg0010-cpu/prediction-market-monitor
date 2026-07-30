"""Manifest-owned release-chain branch selection and materialization."""

# ruff: noqa: EM101, PLR2004, TC003

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.services.release.receipts import canonicalize

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
    verified_receipt,
    write_receipt,
)
from .release_chain_selection import select_manifest_nodes, validate_selected_chain

MANIFEST_KEYS = frozenset({"schema", "segments"})
SEGMENT_KEYS = frozenset({"name", "steps"})
STEP_KEYS = frozenset(
    {
        "id",
        "command",
        "kind",
        "path",
        "attempt_root",
        "node_paths",
        "node_kinds",
        "required",
        "min_attempts",
        "max_attempts",
        "branch_grammar",
        "operation",
        "project_kind",
        "retry_safety_class",
        "expected_predecessor_rule",
    }
)


@dataclass(frozen=True, slots=True)
class MaterializeChainRequest:
    """Typed parser input for ``materialize-chain``."""

    manifest: Path
    receipt_root: Path
    expected_terminal_command: str
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: Path
    json_out: Path


def handle_materialize_chain(
    request: MaterializeChainRequest,
    *,
    io: ReceiptIO,
    clock: Clock,
) -> JsonObject:
    """Select exactly one legal branch and emit consecutive chain ordinals."""
    manifest = validate_chain_manifest(io, request.manifest)
    predecessor = verified_receipt(io, request.predecessor_receipt)
    expected = bindings_of(predecessor)
    _require_request_bindings(request, expected)
    selected = select_manifest_nodes(manifest, request.receipt_root, io)
    if not selected:
        raise ReleaseChainError("manifest_selected_no_nodes")
    validate_selected_chain(selected, expected)
    terminal = selected[-1].receipt
    if terminal["command"] != request.expected_terminal_command:
        raise ReleaseChainError("terminal_command_mismatch")
    if terminal["receipt_sha256"] != predecessor["receipt_sha256"]:
        raise ReleaseChainError("terminal_predecessor_argument_mismatch")
    entries: list[JsonValue] = []
    for ordinal, node in enumerate(selected, start=1):
        entries.append(
            {
                "ordinal": ordinal,
                "step_id": node.step_id,
                "path": node.path,
                "command": node.receipt["command"],
                "receipt_sha256": node.receipt["receipt_sha256"],
                "predecessor_receipt_sha256": node.receipt[
                    "predecessor_receipt_sha256"
                ],
            }
        )
    receipt = build_receipt(
        command="materialize-chain",
        predecessor=predecessor,
        clock=clock,
        details={
            "manifest_sha256": digest(canonicalize(manifest)),
            "node_count": len(entries),
            "nodes": entries,
            "terminal_command": request.expected_terminal_command,
        },
    )
    write_receipt(io, request.json_out, receipt)
    return receipt


def validate_chain_manifest(io: ReceiptIO, path: Path) -> JsonObject:
    """Validate one canonical, schema-closed chain manifest."""
    manifest = load_document(io, path)
    if (
        set(manifest) != set(MANIFEST_KEYS)
        or manifest["schema"] != "release-chain.v1"
    ):
        raise ReleaseChainError("manifest_schema_not_closed")
    segments = manifest["segments"]
    if not isinstance(segments, list) or not segments:
        raise ReleaseChainError("manifest_segments_invalid")
    names: set[str] = set()
    step_ids: set[str] = set()
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) != set(SEGMENT_KEYS):
            raise ReleaseChainError("manifest_segment_schema_not_closed")
        name = segment.get("name")
        if not isinstance(name, str) or name in names:
            raise ReleaseChainError("manifest_segment_duplicate")
        names.add(name)
        steps = segment.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ReleaseChainError("manifest_steps_invalid")
        for step in steps:
            _validate_step(step, step_ids)
    valid_segments = (
        frozenset({"bootstrap", "normal"}),
        frozenset({"release"}),
    )
    if frozenset(names) not in valid_segments:
        raise ReleaseChainError("manifest_segments_wrong")
    return manifest


def _validate_step(value: JsonValue, step_ids: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != set(STEP_KEYS):
        raise ReleaseChainError("manifest_step_schema_not_closed")
    step_id = value.get("id")
    if not isinstance(step_id, str) or step_id in step_ids:
        raise ReleaseChainError("manifest_step_duplicate")
    step_ids.add(step_id)
    kind = value.get("kind")
    if kind not in {"direct", "attempt"}:
        raise ReleaseChainError("manifest_step_kind_invalid")
    if kind == "attempt":
        if value.get("min_attempts") != 1 or value.get("max_attempts") != 2:
            raise ReleaseChainError("attempt_bounds_invalid")
        grammar = value.get("branch_grammar")
        if grammar != ["accepted-1", "failed-1-accepted-2"]:
            raise ReleaseChainError("attempt_grammar_invalid")
        kinds = value.get("node_kinds")
        if not isinstance(kinds, list) or kinds[-1:] != ["verified"]:
            raise ReleaseChainError("attempt_node_kinds_invalid")
        paths = value.get("node_paths")
        if not isinstance(paths, list) or len(kinds) != len(paths):
            raise ReleaseChainError("attempt_node_kinds_mismatch")


def _require_request_bindings(
    request: MaterializeChainRequest,
    expected: Bindings,
) -> None:
    if (
        request.expected_sha != expected.reviewed_sha
        or request.expected_plan_sha256 != expected.approved_plan_sha256
        or request.activation_nonce != expected.activation_nonce
    ):
        raise ReleaseChainError("caller_binding_mismatch")
