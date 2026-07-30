"""Matrix-B projection for the terminal privacy verification handler."""

# ruff: noqa: EM101, PLR2004, TC001, TC003

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol
from uuid import UUID

from scripts.release_chain_common import PathReceiptIO, verified_receipt
from scripts.release_gate_cli_io import read_document
from scripts.release_privacy_contracts import ViolationKind
from scripts.release_privacy_models import MatrixBProof
from scripts.release_vercel_models import verify_receipt
from scripts.runtime_privacy_adapter import PrivacyRuntimeError


class MatrixArgs(Protocol):
    """Typed subset required to validate a terminal Matrix-B chain."""

    matrix_b_health: str
    matrix_b_chain: str
    predecessor_receipt: str
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    expected_current: str
    violation_kind: ViolationKind


def _sha(value: Mapping[str, object]) -> str:
    candidate = value.get("receipt_sha256")
    if not isinstance(candidate, str) or len(candidate) != 64:
        raise PrivacyRuntimeError("privacy_predecessor_invalid")
    return candidate


def _bound(
    value: Mapping[str, object],
    args: MatrixArgs,
) -> None:
    if (
        value.get("reviewed_sha") != args.expected_sha
        or value.get("approved_plan_sha256") != args.expected_plan_sha256
        or value.get("activation_nonce") != args.activation_nonce
    ):
        raise PrivacyRuntimeError("privacy_receipt_binding_mismatch")


def matrix_proof(args: MatrixArgs) -> MatrixBProof:
    """Validate real materialized-chain/health files and project the domain proof."""
    io = PathReceiptIO()
    health = read_document(args.matrix_b_health)
    chain = verified_receipt(io, Path(args.matrix_b_chain))
    predecessor = verified_receipt(io, Path(args.predecessor_receipt))
    _bound(chain, args)
    _bound(predecessor, args)
    health_sha = verify_receipt(
        health,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
    )
    chain_sha = _sha(chain)
    details = chain.get("details")
    if not isinstance(details, dict):
        raise PrivacyRuntimeError("matrix_b_privacy_proof_invalid")
    nodes = details.get("nodes")
    last_matches = (
        isinstance(nodes, list)
        and bool(nodes)
        and isinstance(nodes[-1], dict)
        and nodes[-1].get("receipt_sha256") == health_sha
    )
    terminal = details.get("terminal_command")
    if (
        health.get("command") != "matrix-b-health"
        or health.get("accepted") is not True
        or health.get("state_after") != "restore_writing"
        or health.get("database_revision") != args.expected_current
        or args.expected_current != "20260727_0010"
        or chain.get("command") != "materialize-chain"
        or chain.get("accepted") is not True
        or terminal != "matrix-b-health"
        or not last_matches
        or _sha(predecessor) != chain_sha
    ):
        raise PrivacyRuntimeError("matrix_b_privacy_proof_invalid")
    return MatrixBProof(
        command="matrix-b-terminal-chain",
        accepted=True,
        incident_class=args.violation_kind,
        durable_state="restore_writing",
        database_revision="20260727_0010",
        receipt_sha256=chain_sha,
        health_sha256=health_sha,
    )


__all__ = ("matrix_proof",)
