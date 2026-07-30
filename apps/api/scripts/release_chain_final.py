"""Pure final-lane, parallel fan-in, and aggregate release handlers."""

# ruff: noqa: EM101, PLR2004, TC003

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

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
    require_bindings,
    verified_receipt,
    write_receipt,
)

type FinalLane = Literal["F1", "F2", "F3", "F4", "F4-acceptance"]
PARALLEL_LANES: Final = frozenset({"F1", "F2", "F3"})


@dataclass(frozen=True, slots=True)
class FinalLaneRequest:
    """Typed parser input for ``final-lane``."""

    lane: FinalLane
    report: Path
    production_result: Path
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: Path
    json_out: Path
    aux_report: Path | None = None
    cadence: Path | None = None


@dataclass(frozen=True, slots=True)
class FinalFanInRequest:
    """Typed parser input for ``final-fan-in``."""

    parent: Path
    branches: tuple[Path, ...]
    expected_branches: tuple[str, ...]
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: Path
    json_out: Path


def handle_final_lane(
    request: FinalLaneRequest,
    *,
    io: ReceiptIO,
    clock: Clock,
) -> JsonObject:
    """Turn an APPROVE report into a canonical branch receipt."""
    predecessor = verified_receipt(io, request.predecessor_receipt)
    production = verified_receipt(io, request.production_result)
    expected = bindings_of(predecessor)
    _request_bindings(request, expected)
    require_bindings(production, expected)
    if production["command"] != "production" or production["accepted"] is not True:
        raise ReleaseChainError("production_result_not_accepted")
    if request.lane in PARALLEL_LANES:
        if predecessor["receipt_sha256"] != production["receipt_sha256"]:
            raise ReleaseChainError("parallel_lane_parent_mismatch")
    else:
        if request.cadence is None:
            raise ReleaseChainError("f4_requires_cadence")
        cadence = verified_receipt(io, request.cadence)
        require_bindings(cadence, expected)
        if predecessor["receipt_sha256"] != cadence["receipt_sha256"]:
            raise ReleaseChainError("f4_cadence_parent_mismatch")
    report_sha = _approved_report(io, request.report)
    aux_sha: str | None = None
    if request.lane == "F3":
        if request.aux_report is None:
            raise ReleaseChainError("f3_requires_aux_report")
        aux_sha = digest(io.read(request.aux_report))
    elif request.aux_report is not None:
        raise ReleaseChainError("aux_report_forbidden_for_lane")
    details: JsonObject = {
        "lane": request.lane,
        "report_sha256": report_sha,
        "aux_report_sha256": aux_sha,
        "production_result_sha256": production["receipt_sha256"],
        "cadence_sha256": (
            predecessor["receipt_sha256"]
            if request.lane not in PARALLEL_LANES
            else None
        ),
    }
    receipt = build_receipt(
        command="final-lane",
        predecessor=predecessor,
        clock=clock,
        details=details,
    )
    write_receipt(io, request.json_out, receipt)
    return receipt


def handle_final_fan_in(
    request: FinalFanInRequest,
    *,
    io: ReceiptIO,
    clock: Clock,
) -> JsonObject:
    """Join exactly F1/F2/F3 sharing one immutable production parent."""
    parent = verified_receipt(io, request.parent)
    predecessor = verified_receipt(io, request.predecessor_receipt)
    expected = bindings_of(parent)
    _request_bindings(request, expected)
    if predecessor["receipt_sha256"] != parent["receipt_sha256"]:
        raise ReleaseChainError("fan_in_predecessor_not_parent")
    if tuple(request.expected_branches) != ("F1", "F2", "F3"):
        raise ReleaseChainError("expected_branches_not_exact")
    by_lane: dict[str, JsonObject] = {}
    for path in request.branches:
        branch = verified_receipt(io, path)
        require_bindings(branch, expected)
        details = _details(branch)
        lane = details.get("lane")
        if (
            branch["command"] != "final-lane"
            or lane not in PARALLEL_LANES
            or lane in by_lane
        ):
            raise ReleaseChainError("fan_in_branch_invalid_or_duplicate")
        if branch["predecessor_receipt_sha256"] != parent["receipt_sha256"]:
            raise ReleaseChainError("fan_in_branch_foreign_parent")
        by_lane[str(lane)] = branch
    if set(by_lane) != set(PARALLEL_LANES) or len(request.branches) != 3:
        raise ReleaseChainError("fan_in_branches_not_exact")
    branch_hashes: list[JsonValue] = [
        {"lane": lane, "receipt_sha256": by_lane[lane]["receipt_sha256"]}
        for lane in ("F1", "F2", "F3")
    ]
    receipt = build_receipt(
        command="final-fan-in",
        predecessor=parent,
        clock=clock,
        details={
            "parent_sha256": parent["receipt_sha256"],
            "branch_count": 3,
            "branches": branch_hashes,
        },
    )
    write_receipt(io, request.json_out, receipt)
    return receipt


def _approved_report(io: ReceiptIO, path: Path) -> str:
    raw = io.read(path)
    try:
        first = raw.decode().splitlines()[0].strip()
    except (UnicodeDecodeError, IndexError) as error:
        raise ReleaseChainError("report_not_utf8") from error
    if first != "APPROVE":
        raise ReleaseChainError("report_not_approved")
    return digest(raw)


def _details(receipt: JsonObject) -> JsonObject:
    details = receipt.get("details")
    if not isinstance(details, dict):
        raise ReleaseChainError("receipt_details_invalid")
    return details


def _request_bindings(
    request: FinalLaneRequest | FinalFanInRequest,
    expected: Bindings,
) -> None:
    if (
        request.expected_sha != expected.reviewed_sha
        or request.expected_plan_sha256 != expected.approved_plan_sha256
        or request.activation_nonce != expected.activation_nonce
    ):
        raise ReleaseChainError("caller_binding_mismatch")
