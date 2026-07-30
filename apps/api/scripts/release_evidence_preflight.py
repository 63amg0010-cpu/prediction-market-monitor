"""Credential-free no-spend bootstrap decision."""

# ruff: noqa: EM101, PLR0913, TC003

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID

from scripts.release_evidence_contracts import (
    PRE_0010_KINDS,
    EvidenceHoldError,
    ReviewRecordAccess,
)
from scripts.release_evidence_graph import canonical_bytes, receipt_sha256
from scripts.release_evidence_preflight_checks import (
    evidence_time,
    require_bindings,
    require_free_tier,
    require_fresh,
    require_graph_content,
    require_provider_captures,
)
from scripts.release_evidence_review import validate_review_record


def no_spend_preflight(
    *,
    review_record: Mapping[str, object],
    review_access: ReviewRecordAccess,
    live_plan_path: str,
    live_plan_bytes: bytes,
    expected_sha: str,
    activation_nonce: UUID,
    deployment_prestate: Mapping[str, object],
    evidence_join_receipt: Mapping[str, object],
    provider_captures: Sequence[Mapping[str, object]],
    production_measurements: Mapping[str, object],
    free_tier_result: Mapping[str, object],
    predecessor_receipt: Mapping[str, object],
    bootstrap_attempt_exists: bool,
) -> dict[str, object]:
    """Accept only the one credential-free protected 0010 bootstrap."""
    if bootstrap_attempt_exists:
        raise EvidenceHoldError("bootstrap_already_attempted")
    bindings = validate_review_record(
        review_record,
        access=review_access,
        live_plan_path=live_plan_path,
        live_plan_bytes=live_plan_bytes,
        expected_sha=expected_sha,
    )
    if (
        deployment_prestate.get("command") != "deployment-prestate"
        or deployment_prestate.get("accepted") is not True
        or deployment_prestate.get("predecessor_receipt_sha256") is not None
    ):
        raise EvidenceHoldError("deployment_prestate_rejected")
    launches = deployment_prestate.get("approval_launch_sha256s")
    if (
        deployment_prestate.get("approval_round_id") != bindings.approval_round_id
        or not isinstance(launches, list)
        or tuple(cast("list[object]", launches)) != bindings.approval_launch_sha256s
    ):
        raise EvidenceHoldError("review_approval_binding_mismatch")
    require_bindings(
        deployment_prestate,
        reviewed_sha=expected_sha,
        plan_sha=bindings.approved_plan_sha256,
        activation_nonce=activation_nonce,
    )
    branch_kinds = evidence_join_receipt.get("branch_kinds")
    if (
        evidence_join_receipt.get("command") != "evidence-join"
        or not isinstance(branch_kinds, list)
        or tuple(cast("list[object]", branch_kinds)) != PRE_0010_KINDS
        or evidence_join_receipt.get("predecessor_receipt_sha256")
        != receipt_sha256(deployment_prestate)
    ):
        raise EvidenceHoldError("pre_0010_evidence_graph_rejected")
    db_now = evidence_time(free_tier_result.get("db_now"))
    require_provider_captures(
        provider_captures,
        reviewed_sha=expected_sha,
        plan_sha=bindings.approved_plan_sha256,
        activation_nonce=activation_nonce,
        db_now=db_now,
    )
    if (
        production_measurements.get("transaction_read_only") is not True
        or production_measurements.get("sampled") is not False
    ):
        raise EvidenceHoldError("production_measurement_rejected")
    require_fresh(production_measurements, db_now)
    require_graph_content(
        evidence_join_receipt,
        provider_captures,
        production_measurements,
        free_tier_result,
    )
    require_free_tier(
        free_tier_result,
        evidence_join_receipt=evidence_join_receipt,
        reviewed_sha=expected_sha,
        plan_sha=bindings.approved_plan_sha256,
        activation_nonce=activation_nonce,
    )
    if canonical_bytes(predecessor_receipt) != canonical_bytes(free_tier_result):
        raise EvidenceHoldError("no_spend_predecessor_mismatch")
    return {
        **dict(deployment_prestate),
        "command": "no-spend-preflight",
        "billing_disabled": True,
        "projection_below_70_percent": True,
        "operation_scope": "migrate-0010-bootstrap-only",
        "provider_capture_sha256s": [
            receipt_sha256(capture) for capture in provider_captures
        ],
        "production_measurements_sha256": receipt_sha256(production_measurements),
        "free_tier_result_sha256": receipt_sha256(free_tier_result),
        "predecessor_receipt_sha256": receipt_sha256(free_tier_result),
    }


__all__ = ("no_spend_preflight",)
