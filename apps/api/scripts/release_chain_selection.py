"""Attempt grammar and predecessor selection for release manifests."""

# ruff: noqa: C901, EM101, PLR0913, TC003

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .release_chain_common import (
    Bindings,
    JsonObject,
    JsonValue,
    ReceiptIO,
    ReleaseChainError,
    require_bindings,
    verified_receipt,
)


@dataclass(frozen=True, slots=True)
class SelectedNode:
    """One selected physical receipt with its manifest identity."""

    step_id: str
    path: str
    receipt: JsonObject


def select_manifest_nodes(
    manifest: JsonObject,
    root: Path,
    io: ReceiptIO,
) -> list[SelectedNode]:
    """Select all required direct nodes and one legal branch per attempt."""
    result: list[SelectedNode] = []
    seen_paths: set[str] = set()
    segments = manifest["segments"]
    if not isinstance(segments, list):
        raise ReleaseChainError("manifest_segments_invalid")
    for segment in segments:
        if not isinstance(segment, dict):
            raise ReleaseChainError("manifest_segment_invalid")
        steps = segment.get("steps")
        if not isinstance(steps, list):
            raise ReleaseChainError("manifest_steps_invalid")
        for raw_step in steps:
            if not isinstance(raw_step, dict):
                raise ReleaseChainError("manifest_step_invalid")
            selected = _select_step(raw_step, root, io)
            if not selected and raw_step["required"] is True:
                raise ReleaseChainError("required_step_omitted")
            for node in selected:
                if node.path in seen_paths:
                    raise ReleaseChainError("duplicate_receipt_path")
                seen_paths.add(node.path)
                result.append(node)
    return result


def validate_selected_chain(
    nodes: list[SelectedNode],
    expected: Bindings,
) -> None:
    """Require unique hashes and consecutive predecessor references."""
    previous: str | None = None
    seen_hashes: set[str] = set()
    for index, node in enumerate(nodes):
        receipt = node.receipt
        require_bindings(receipt, expected)
        receipt_sha = str(receipt["receipt_sha256"])
        if receipt_sha in seen_hashes:
            raise ReleaseChainError("duplicate_receipt_hash")
        seen_hashes.add(receipt_sha)
        predecessor = receipt["predecessor_receipt_sha256"]
        if index == 0:
            if predecessor is not None and node.step_id == "deployment-prestate":
                raise ReleaseChainError("root_predecessor_not_null")
        elif predecessor != previous:
            raise ReleaseChainError("nonconsecutive_predecessor")
        previous = receipt_sha


def _select_step(
    step: dict[str, JsonValue],
    root: Path,
    io: ReceiptIO,
) -> list[SelectedNode]:
    step_id = str(step["id"])
    if step["kind"] == "direct":
        path = str(step["path"])
        target = root / path
        if not target.exists():
            return []
        receipt = verified_receipt(io, target)
        if receipt["accepted"] is not True or receipt["command"] != step["command"]:
            raise ReleaseChainError("direct_step_not_accepted")
        return [SelectedNode(step_id, path, receipt)]
    attempt_root = str(step["attempt_root"])
    node_paths = step["node_paths"]
    if not isinstance(node_paths, list) or not all(
        isinstance(item, str) for item in node_paths
    ):
        raise ReleaseChainError("attempt_node_paths_invalid")
    paths = [str(item) for item in node_paths]
    attempt1 = _attempt_nodes(step_id, attempt_root, 1, paths, root, io)
    attempt2 = _attempt_nodes(step_id, attempt_root, 2, paths, root, io)
    if not attempt1:
        if attempt2:
            raise ReleaseChainError("orphan_attempt_2")
        return []
    final1 = attempt1[-1].receipt
    if final1["command"] != step["command"]:
        raise ReleaseChainError("attempt_terminal_command_mismatch")
    if final1["accepted"] is True:
        if attempt2 or not all(node.receipt["accepted"] is True for node in attempt1):
            raise ReleaseChainError("extra_attempt_after_acceptance")
        return attempt1
    retryable = (
        final1["terminal_for_attempt"] is True
        and final1["retry_permitted"] is True
    )
    if not retryable:
        raise ReleaseChainError("attempt_1_not_retryable")
    if not all(node.receipt["accepted"] is True for node in attempt1[:-1]):
        raise ReleaseChainError("attempt_1_prefix_not_accepted")
    if (
        not attempt2
        or attempt2[-1].receipt["command"] != step["command"]
        or not all(node.receipt["accepted"] is True for node in attempt2)
    ):
        raise ReleaseChainError("attempt_2_not_accepted")
    return [*attempt1, *attempt2]


def _attempt_nodes(
    step_id: str,
    attempt_root: str,
    attempt: int,
    node_paths: list[str],
    root: Path,
    io: ReceiptIO,
) -> list[SelectedNode]:
    paths = [f"{attempt_root}/attempt-{attempt}/{name}" for name in node_paths]
    present = [(root / path).exists() for path in paths]
    if not any(present):
        return []
    if not all(present):
        raise ReleaseChainError("partial_attempt_nodes")
    nodes = [
        SelectedNode(step_id, path, verified_receipt(io, root / path))
        for path in paths
    ]
    if any(node.receipt["attempt"] != attempt for node in nodes):
        raise ReleaseChainError("attempt_number_mismatch")
    return nodes
