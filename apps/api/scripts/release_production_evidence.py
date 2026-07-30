"""Canonical local evidence validation for the Production release gate."""

# ruff: noqa: EM101, PLR2004

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, cast

from apps.api.scripts.free_tier_domain import JsonObject, JsonValue, canonical_bytes

from .release_chain_common import Bindings, ReceiptIO, ReleaseChainError

if TYPE_CHECKING:
    from pathlib import Path

type Document = JsonObject
HEX = frozenset("0123456789abcdef")
ATTESTATION_KEYS = frozenset(
    {
        "schema_version",
        "command",
        "reviewed_sha",
        "activation_nonce",
        "attestation_generation",
        "database_time",
        "authorization_evidence_sha256",
        "free_tier_evidence_sha256",
        "provenance_sha256",
        "predecessor_receipt_sha256",
        "redacted_ratios",
        "public_evidence_urls",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceDigests:
    """Hashes that the durable database observation must reproduce."""

    attestation_sha256: str
    free_tier_sha256: str
    attestation_generation: int


def validate_evidence(
    io: ReceiptIO,
    *,
    attestation_path: Path,
    free_tier_path: Path,
    bindings: Bindings,
) -> EvidenceDigests:
    """Validate canonical, schema-closed attestation and free-tier inputs."""
    attestation_raw, attestation = _load(io, attestation_path)
    free_raw, free_tier = _load(io, free_tier_path)
    _validate_free_tier(free_tier, bindings)
    _validate_attestation(attestation, bindings, sha256(free_raw).hexdigest())
    generation = attestation["attestation_generation"]
    if not isinstance(generation, int) or isinstance(generation, bool):
        raise ReleaseChainError("attestation_generation_invalid")
    return EvidenceDigests(
        sha256(attestation_raw).hexdigest(),
        sha256(free_raw).hexdigest(),
        generation,
    )


def _load(io: ReceiptIO, path: Path) -> tuple[bytes, Document]:
    try:
        raw = io.read(path)
    except OSError as error:
        raise ReleaseChainError("evidence_missing") from error
    try:
        parsed = cast(
            "object",
            json.loads(raw, object_pairs_hook=_unique_object),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleaseChainError("evidence_invalid_json") from error
    if not isinstance(parsed, dict):
        raise ReleaseChainError("evidence_root_not_object")
    document = cast("Document", parsed)
    try:
        if raw != canonical_bytes(document):
            raise ReleaseChainError("evidence_noncanonical")
    except (TypeError, ValueError) as error:
        raise ReleaseChainError("evidence_noncanonical") from error
    return raw, document


def _validate_attestation(
    value: Document,
    bindings: Bindings,
    expected_free_tier_sha: str,
) -> None:
    if set(value) != set(ATTESTATION_KEYS):
        raise ReleaseChainError("attestation_schema_not_closed")
    if (
        value.get("schema_version") != 1
        or value.get("command") != "activation-attestation"
        or value.get("reviewed_sha") != bindings.reviewed_sha
        or value.get("activation_nonce") != bindings.activation_nonce
        or value.get("free_tier_evidence_sha256") != expected_free_tier_sha
    ):
        raise ReleaseChainError("attestation_binding_mismatch")
    generation = value.get("attestation_generation")
    if (
        not isinstance(generation, int)
        or isinstance(generation, bool)
        or generation < 1
    ):
        raise ReleaseChainError("attestation_generation_invalid")
    for name in (
        "authorization_evidence_sha256",
        "free_tier_evidence_sha256",
        "provenance_sha256",
        "predecessor_receipt_sha256",
    ):
        if not _hex(value.get(name)):
            raise ReleaseChainError("attestation_digest_invalid")
    ratios = value.get("redacted_ratios")
    if not isinstance(ratios, list) or not ratios:
        raise ReleaseChainError("attestation_ratios_missing")


def _validate_free_tier(value: Document, bindings: Bindings) -> None:
    required = {
        "schema",
        "accepted",
        "phase",
        "reviewed_sha",
        "expected_plan_sha256",
        "activation_nonce",
        "dimensions",
        "receipt_sha256",
    }
    if not required.issubset(value):
        raise ReleaseChainError("free_tier_fields_missing")
    if (
        value.get("schema") != "free-tier.result.v1"
        or value.get("accepted") is not True
        or value.get("phase") != "pre-0010"
        or value.get("reviewed_sha") != bindings.reviewed_sha
        or value.get("expected_plan_sha256") != bindings.approved_plan_sha256
        or value.get("activation_nonce") != bindings.activation_nonce
    ):
        raise ReleaseChainError("free_tier_binding_mismatch")
    receipt_sha = value.get("receipt_sha256")
    body = {key: item for key, item in value.items() if key != "receipt_sha256"}
    expected_sha = sha256(canonical_bytes(body)).hexdigest()
    if not _hex(receipt_sha) or receipt_sha != expected_sha:
        raise ReleaseChainError("free_tier_receipt_hash_mismatch")
    dimensions = value.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise ReleaseChainError("free_tier_dimensions_missing")
    for item in cast("list[object]", dimensions):
        if not isinstance(item, dict):
            raise ReleaseChainError("free_tier_dimension_invalid")
        ratio = cast("dict[str, object]", item).get("ratio")
        if (
            not isinstance(ratio, (int, float))
            or isinstance(ratio, bool)
            or ratio < 0
            or ratio >= 0.70
        ):
            raise ReleaseChainError("free_tier_dimension_unsafe")


def _hex(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def _unique_object(pairs: list[tuple[str, object]]) -> Document:
    result: Document = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseChainError("evidence_duplicate_key")
        result[key] = cast("JsonValue", value)
    return result


__all__ = ("EvidenceDigests", "validate_evidence")
