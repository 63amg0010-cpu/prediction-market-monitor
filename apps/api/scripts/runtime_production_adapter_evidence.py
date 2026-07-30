"""Offline proof preparation for the concrete Production probe."""

# pyright: reportArgumentType=false
# ruff: noqa: D101, EM101, PLR2004

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from app.services.release.receipts import canonicalize

from .release_chain_common import (
    Bindings,
    JsonObject,
    PathReceiptIO,
    ReleaseChainError,
    bindings_of,
    load_document,
    require_bindings,
    verified_receipt,
)
from .release_production_evidence import validate_evidence
from .release_production_validation import PROJECTS, validate_request

if TYPE_CHECKING:
    from .release_production_models import ProductionRequest


@dataclass(frozen=True, slots=True)
class PreparedDeployment:
    kind: str
    project_name: str
    project_identity_sha256: str
    deployment_identity_sha256: str
    team_identity_sha256: str
    state: str
    production: bool
    reviewed_sha: str


@dataclass(frozen=True, slots=True)
class PreparedEvidence:
    bindings: Bindings
    release_chain_sha256: str
    attestation_sha256: str
    free_tier_sha256: str
    deployments: tuple[PreparedDeployment, PreparedDeployment]


def prepare_evidence(request: ProductionRequest) -> PreparedEvidence:
    """Validate every local proof before an engine or HTTP client can run."""
    _reject_test_proof(request)
    io = PathReceiptIO()
    chain = _verified(io, request.release_chain)
    bindings = bindings_of(chain)
    validate_request(request, bindings)
    _require_terminal(chain)
    release_raw = _read(request.release_chain)
    evidence = validate_evidence(
        io,
        attestation_path=request.attestation,
        free_tier_path=request.free_tier_result,
        bindings=bindings,
    )
    documents = _walk_chain(request.release_chain, chain, bindings, io)
    deployments = derive_deployments(documents, bindings.reviewed_sha)
    return PreparedEvidence(
        bindings,
        sha256(release_raw).hexdigest(),
        evidence.attestation_sha256,
        evidence.free_tier_sha256,
        deployments,
    )


def _walk_chain(
    chain_path: Path,
    chain: JsonObject,
    bindings: Bindings,
    io: PathReceiptIO,
) -> tuple[JsonObject, ...]:
    root = chain_path.resolve().parent
    pending = [chain]
    result: list[JsonObject] = []
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        result.append(current)
        details = current.get("details")
        nodes = details.get("nodes") if isinstance(details, dict) else None
        if not isinstance(nodes, list):
            continue
        for raw in nodes:
            if not isinstance(raw, dict):
                raise ReleaseChainError("release_chain_node_invalid")
            relative = raw.get("path")
            expected = raw.get("receipt_sha256")
            target = _bounded_path(root, relative)
            document = load_document(io, target)
            _verify_document(document, expected, bindings)
            digest = cast("str", document["receipt_sha256"])
            if digest not in seen:
                seen.add(digest)
                pending.append(document)
    return tuple(result)


def _verify_document(
    document: JsonObject,
    expected: object,
    bindings: Bindings,
) -> None:
    claimed = document.get("receipt_sha256")
    if not isinstance(claimed, str) or claimed != expected:
        raise ReleaseChainError("release_chain_node_hash_mismatch")
    body = {key: value for key, value in document.items() if key != "receipt_sha256"}
    if sha256(canonicalize(body)).hexdigest() != claimed:
        raise ReleaseChainError("release_chain_node_receipt_invalid")
    if "approved_plan_sha256" in document:
        require_bindings(document, bindings)
    if document.get("accepted") is not True:
        raise ReleaseChainError("release_chain_node_not_accepted")


def derive_deployments(
    documents: tuple[JsonObject, ...],
    reviewed_sha: str,
) -> tuple[PreparedDeployment, PreparedDeployment]:
    """Derive the exact redacted API/Web deployment set from the chain."""
    composite = next(
        (
            item
            for item in documents
            if item.get("command") in {"deployment-prestate", "vercel-prestate"}
            and isinstance(item.get("projects"), list)
        ),
        None,
    )
    if composite is None:
        raise ReleaseChainError("production_deployment_proof_missing")
    team = composite.get("team_identity_sha256")
    projects = cast("list[object]", composite["projects"])
    indexed: dict[str, PreparedDeployment] = {}
    for raw in projects:
        if not isinstance(raw, dict):
            raise ReleaseChainError("production_deployment_proof_invalid")
        item = cast("dict[str, object]", raw)
        kind = item.get("kind")
        name = item.get("project_name")
        if kind not in PROJECTS or name != PROJECTS[kind]:
            raise ReleaseChainError("production_deployment_identity_wrong")
        indexed[cast("str", kind)] = PreparedDeployment(
            cast("str", kind),
            cast("str", name),
            _hex(item.get("project_identity_sha256")),
            _hex(item.get("deployment_identity_sha256")),
            _hex(team),
            cast("str", item.get("ready_state")),
            item.get("environment") == "production",
            cast("str", item.get("protected_source_sha")),
        )
    if set(indexed) != set(PROJECTS):
        raise ReleaseChainError("production_deployment_set_not_exact")
    if any(value.reviewed_sha != reviewed_sha for value in indexed.values()):
        raise ReleaseChainError("production_deployment_sha_wrong")
    return indexed["api"], indexed["web"]


def _bounded_path(root: Path, value: object) -> Path:
    if not isinstance(value, str):
        raise ReleaseChainError("release_chain_node_path_invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseChainError("release_chain_node_path_invalid")
    target = (root / Path(*relative.parts)).resolve(strict=True)
    try:
        _ = target.relative_to(root)
    except ValueError as error:
        raise ReleaseChainError("release_chain_node_path_invalid") from error
    return target


def _hex(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ReleaseChainError("production_deployment_identity_invalid")
    return value


def _require_terminal(chain: JsonObject) -> None:
    details = chain.get("details")
    if (
        chain.get("command") != "materialize-chain"
        or chain.get("accepted") is not True
        or not isinstance(details, dict)
        or details.get("terminal_command") != "cadence-initial"
    ):
        raise ReleaseChainError("release_chain_incomplete")


def _reject_test_proof(request: ProductionRequest) -> None:
    for path in (request.release_chain, request.attestation, request.free_tier_result):
        lowered = {part.lower() for part in path.parts}
        if any("fixture" in part or "stub" in part for part in lowered):
            raise ReleaseChainError("nonproduction_evidence")


def _verified(io: PathReceiptIO, path: Path) -> JsonObject:
    try:
        return verified_receipt(io, path)
    except OSError as error:
        raise ReleaseChainError("release_receipt_missing") from error


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as error:
        raise ReleaseChainError("release_chain_missing") from error


__all__ = (
    "PreparedDeployment",
    "PreparedEvidence",
    "derive_deployments",
    "prepare_evidence",
)
