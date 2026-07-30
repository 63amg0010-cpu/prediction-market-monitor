"""Day-zero and 30-day final aggregate ordering."""

# ruff: noqa: C901, EM101, PLR2004, TC003

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .release_chain_common import (
    Bindings,
    Clock,
    JsonObject,
    JsonValue,
    ReceiptIO,
    ReleaseChainError,
    bindings_of,
    build_receipt,
    require_bindings,
    verified_receipt,
    write_receipt,
)


@dataclass(frozen=True, slots=True)
class AggregateRequest:
    """Typed parser input for day-zero or 30-day ``aggregate``."""

    fan_in: Path
    f4: Path
    cadence: Path
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: Path
    json_out: Path
    acceptance_refresh: Path | None = None


def handle_aggregate(
    request: AggregateRequest,
    *,
    io: ReceiptIO,
    clock: Clock,
) -> JsonObject:
    """Enforce fan-in → cadence → F4 ordering and emit final status."""
    fan_in = verified_receipt(io, request.fan_in)
    cadence = verified_receipt(io, request.cadence)
    f4 = verified_receipt(io, request.f4)
    predecessor = verified_receipt(io, request.predecessor_receipt)
    expected = bindings_of(f4)
    _request_bindings(request, expected)
    for receipt in (fan_in, cadence, f4, predecessor):
        require_bindings(receipt, expected)
    if fan_in["command"] != "final-fan-in":
        raise ReleaseChainError("aggregate_fan_in_invalid")
    if (
        f4["command"] != "final-lane"
        or predecessor["receipt_sha256"] != f4["receipt_sha256"]
    ):
        raise ReleaseChainError("aggregate_predecessor_not_f4")
    f4_lane = _details(f4).get("lane")
    status = "HOLD"
    acceptance_sha: JsonValue = None
    if request.acceptance_refresh is None:
        if cadence["predecessor_receipt_sha256"] != fan_in["receipt_sha256"]:
            raise ReleaseChainError("day_zero_cadence_order_invalid")
        if f4_lane != "F4":
            raise ReleaseChainError("day_zero_f4_lane_invalid")
    else:
        refresh = verified_receipt(io, request.acceptance_refresh)
        require_bindings(refresh, expected)
        if refresh["command"] != "acceptance-refresh":
            raise ReleaseChainError("acceptance_refresh_invalid")
        if cadence["predecessor_receipt_sha256"] != refresh["receipt_sha256"]:
            raise ReleaseChainError("acceptance_cadence_order_invalid")
        if f4_lane != "F4-acceptance":
            raise ReleaseChainError("acceptance_f4_lane_invalid")
        if _details(refresh).get("member_count") != 15:
            raise ReleaseChainError("acceptance_members_not_15")
        status = "COMPLETE"
        acceptance_sha = refresh["receipt_sha256"]
    if f4["predecessor_receipt_sha256"] != cadence["receipt_sha256"]:
        raise ReleaseChainError("f4_not_cadence_successor")
    receipt = build_receipt(
        command="aggregate",
        predecessor=f4,
        clock=clock,
        details={
            "status": status,
            "fan_in_sha256": fan_in["receipt_sha256"],
            "cadence_sha256": cadence["receipt_sha256"],
            "f4_sha256": f4["receipt_sha256"],
            "acceptance_refresh_sha256": acceptance_sha,
        },
    )
    write_receipt(io, request.json_out, receipt)
    return receipt


def _details(receipt: JsonObject) -> JsonObject:
    details = receipt.get("details")
    if not isinstance(details, dict):
        raise ReleaseChainError("receipt_details_invalid")
    return details


def _request_bindings(request: AggregateRequest, expected: Bindings) -> None:
    if (
        request.expected_sha != expected.reviewed_sha
        or request.expected_plan_sha256 != expected.approved_plan_sha256
        or request.activation_nonce != expected.activation_nonce
    ):
        raise ReleaseChainError("caller_binding_mismatch")
