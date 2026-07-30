"""Injected, read-only Production acceptance handler."""

# ruff: noqa: EM101

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from .release_chain_common import (
    Clock,
    JsonObject,
    ReceiptIO,
    ReleaseChainError,
    bindings_of,
    build_receipt,
    require_bindings,
    verified_receipt,
    write_receipt,
)
from .release_production_evidence import validate_evidence
from .release_production_models import (
    ProductionProbe,
    ProductionProbeQuery,
    ProductionRequest,
)
from .release_production_validation import validate_observation, validate_request

if TYPE_CHECKING:
    from pathlib import Path


def handle_production(
    request: ProductionRequest,
    *,
    io: ReceiptIO,
    clock: Clock,
    probe: ProductionProbe,
) -> JsonObject:
    """Verify one live Production snapshot and emit a redacted chain receipt."""
    predecessor = _verified(io, request.predecessor_receipt)
    release_chain = _verified(io, request.release_chain)
    bindings = bindings_of(release_chain)
    require_bindings(predecessor, bindings)
    validate_request(request, bindings)
    release_raw = _read(io, request.release_chain, "release_chain_missing")
    release_sha = sha256(release_raw).hexdigest()
    _validate_chain_mode(request, predecessor, release_chain, release_sha)
    evidence = validate_evidence(
        io,
        attestation_path=request.attestation,
        free_tier_path=request.free_tier_result,
        bindings=bindings,
    )
    observation = probe.observe(
        ProductionProbeQuery(
            database_url_env=request.database_url_env,
            api_url=request.api_url,
            web_url=request.web_url,
            expected_sha=request.expected_sha,
            expected_revision=request.expected_revision,
        )
    )
    details = validate_observation(
        request,
        observation,
        bindings,
        evidence,
        release_sha,
    )
    receipt = build_receipt(
        command="production",
        predecessor=predecessor,
        clock=clock,
        details=details,
    )
    write_receipt(io, request.json_out, receipt)
    return receipt


def _validate_chain_mode(
    request: ProductionRequest,
    predecessor: JsonObject,
    release_chain: JsonObject,
    release_sha: str,
) -> None:
    details = release_chain.get("details")
    if (
        release_chain.get("command") != "materialize-chain"
        or release_chain.get("accepted") is not True
        or not isinstance(details, dict)
        or details.get("terminal_command") != "cadence-initial"
    ):
        raise ReleaseChainError("release_chain_incomplete")
    release_receipt_sha = release_chain["receipt_sha256"]
    if request.read_only:
        predecessor_details = predecessor.get("details")
        if (
            predecessor.get("command") != "production"
            or predecessor.get("accepted") is not True
            or predecessor.get("predecessor_receipt_sha256") != release_receipt_sha
            or not isinstance(predecessor_details, dict)
            or predecessor_details.get("release_chain_sha256") != release_sha
        ):
            raise ReleaseChainError("f3_predecessor_not_production")
    elif predecessor["receipt_sha256"] != release_receipt_sha:
        raise ReleaseChainError("todo12_predecessor_not_release_chain")


def _verified(io: ReceiptIO, path: Path) -> JsonObject:
    try:
        return verified_receipt(io, path)
    except OSError as error:
        raise ReleaseChainError("release_receipt_missing") from error


def _read(io: ReceiptIO, path: Path, error_code: str) -> bytes:
    try:
        return io.read(path)
    except OSError as error:
        raise ReleaseChainError(error_code) from error


__all__ = ("handle_production",)
