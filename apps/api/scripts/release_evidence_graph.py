"""RFC 8785 content-addressed evidence branches and join."""

# ruff: noqa: EM101, PLR0913, TC003

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final
from uuid import UUID

from pydantic import JsonValue, TypeAdapter, ValidationError

from scripts.free_tier_domain import canonical_bytes as _jcs_bytes
from scripts.release_evidence_contracts import (
    EVIDENCE_KINDS,
    EvidenceHoldError,
)

type JsonObject = dict[str, JsonValue]

_DOCUMENT: Final[TypeAdapter[JsonObject]] = TypeAdapter(JsonObject)


def _document(value: Mapping[str, object]) -> JsonObject:
    try:
        return _DOCUMENT.validate_python(dict(value))
    except ValidationError as error:
        raise EvidenceHoldError("json_document_rejected") from error


def canonical_bytes(value: Mapping[str, object]) -> bytes:
    """Return RFC 8785 bytes for the schema-root JSON object."""
    return _jcs_bytes(_document(value))


def receipt_sha256(value: Mapping[str, object]) -> str:
    """Hash all canonical receipt bytes; receipts never self-embed a digest."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def canonical_hash(document: Mapping[str, object]) -> JsonObject:
    """Create the immutable leaf-hash receipt used by an import branch."""
    digest = hashlib.sha256(canonical_bytes(document)).hexdigest()
    return {
        "schema_version": 1,
        "command": "canonical-hash",
        "input_sha256": digest,
        "accepted": True,
    }


def _require_bindings(
    document: Mapping[str, object],
    *,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: UUID,
) -> None:
    plan = document.get(
        "approved_plan_sha256",
        document.get("expected_plan_sha256"),
    )
    if document.get("reviewed_sha") != expected_sha:
        raise EvidenceHoldError("reviewed_sha_mismatch")
    if plan != expected_plan_sha256:
        raise EvidenceHoldError("approved_plan_sha256_mismatch")
    if document.get("activation_nonce") != str(activation_nonce):
        raise EvidenceHoldError("activation_nonce_mismatch")


def _content_addressed_path(path: Path, kind: str, digest: str) -> None:
    if path.name != f"{digest}.json" or path.parent.name != kind:
        raise EvidenceHoldError("content_addressed_output_required")


def evidence_import(
    *,
    kind: str,
    document: Mapping[str, object],
    expected_input_sha256: str,
    output_path: Path,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: UUID,
    predecessor_receipt: Mapping[str, object],
) -> JsonObject:
    """Bind one immutable leaf to a SHA-indexed import receipt."""
    if kind not in EVIDENCE_KINDS:
        raise EvidenceHoldError("evidence_kind_rejected")
    _content_addressed_path(output_path, kind, expected_input_sha256)
    actual_hash = hashlib.sha256(canonical_bytes(document)).hexdigest()
    predecessor_hash = canonical_hash(document)
    valid_predecessor = (
        predecessor_receipt.get("command") == "canonical-hash"
        and predecessor_receipt.get("accepted") is True
        and predecessor_receipt.get("input_sha256") == actual_hash
        and hmac.compare_digest(
            canonical_bytes(predecessor_receipt),
            canonical_bytes(predecessor_hash),
        )
    )
    if not hmac.compare_digest(actual_hash, expected_input_sha256):
        raise EvidenceHoldError("input_sha256_mismatch")
    if not valid_predecessor:
        raise EvidenceHoldError("canonical_hash_predecessor_mismatch")
    _require_bindings(
        document,
        expected_sha=expected_sha,
        expected_plan_sha256=expected_plan_sha256,
        activation_nonce=activation_nonce,
    )
    return {
        "schema_version": 1,
        "command": "evidence-import",
        "kind": kind,
        "reviewed_sha": expected_sha,
        "approved_plan_sha256": expected_plan_sha256,
        "activation_nonce": str(activation_nonce),
        "input_sha256": actual_hash,
        "content_addressed_path": output_path.as_posix(),
        "accepted": True,
        "predecessor_receipt_sha256": receipt_sha256(predecessor_receipt),
    }


def evidence_join(
    *,
    deployment_root: Mapping[str, object],
    branches: Sequence[Mapping[str, object]],
    expected_branches: Sequence[str],
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: UUID,
    predecessor_receipt: Mapping[str, object],
) -> JsonObject:
    """Join one root and exactly one verified branch of each expected kind."""
    expected = tuple(expected_branches)
    kinds = tuple(branch.get("kind") for branch in branches)
    if (
        not expected
        or len(expected) != len(set(expected))
        or any(kind not in EVIDENCE_KINDS for kind in expected)
        or kinds != expected
    ):
        raise EvidenceHoldError("branch_set_mismatch")
    if canonical_bytes(deployment_root) != canonical_bytes(predecessor_receipt):
        raise EvidenceHoldError("deployment_root_predecessor_mismatch")
    if (
        deployment_root.get("command") != "deployment-prestate"
        or deployment_root.get("accepted") is not True
        or deployment_root.get("predecessor_receipt_sha256") is not None
    ):
        raise EvidenceHoldError("deployment_root_rejected")
    _require_bindings(
        deployment_root,
        expected_sha=expected_sha,
        expected_plan_sha256=expected_plan_sha256,
        activation_nonce=activation_nonce,
    )
    for branch in branches:
        if (
            branch.get("command") != "evidence-import"
            or branch.get("accepted") is not True
        ):
            raise EvidenceHoldError("branch_not_verified")
        _require_bindings(
            branch,
            expected_sha=expected_sha,
            expected_plan_sha256=expected_plan_sha256,
            activation_nonce=activation_nonce,
        )
        path = Path(str(branch.get("content_addressed_path", "")))
        _content_addressed_path(path, str(branch["kind"]), str(branch["input_sha256"]))
    return _document(
        {
            **dict(deployment_root),
            "command": "evidence-join",
            "branch_kinds": list(expected),
            "branch_input_sha256s": {
                str(branch["kind"]): str(branch["input_sha256"]) for branch in branches
            },
            "branch_receipt_sha256s": {
                str(branch["kind"]): receipt_sha256(branch) for branch in branches
            },
            "predecessor_receipt_sha256": receipt_sha256(deployment_root),
        }
    )


__all__ = (
    "canonical_bytes",
    "canonical_hash",
    "evidence_import",
    "evidence_join",
    "receipt_sha256",
)
