"""Canonical public receipt construction for workflow reservation claims."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from .receipts import canonicalize
from .workflow_claims import (
    WorkflowDispatchClaimReceipt,
    WorkflowDispatchClaimRequest,
)


def build_claim_receipt(
    request: WorkflowDispatchClaimRequest,
    row: dict[str, object],
) -> WorkflowDispatchClaimReceipt:
    """Bind database-owned reservation values to the authenticated request."""
    claimed_at = row["claimed_at_db"]
    if not isinstance(claimed_at, datetime):
        error_code = "claimed_database_time_invalid"
        raise TypeError(error_code)
    body = {
        "schema": "release-chain-receipt.v1",
        "command": "workflow-dispatch-claim",
        "reviewed_sha": row["reviewed_sha"],
        "approved_plan_sha256": row["approved_plan_sha256"],
        "approval_round_id": row["approval_round_id"],
        "approval_launch_sha256s": row["approval_launch_sha256s"],
        "activation_nonce": row["activation_nonce"],
        "dispatch_nonce": row["dispatch_nonce"],
        "attempt": row["attempt"],
        "database_timestamps": {
            "created_at_db": claimed_at,
            "reserved_at_db": row["reserved_at_db"],
            "selection_floor_at": row["selection_floor_at"],
            "claimed_at_db": claimed_at,
        },
        "accepted": True,
        "terminal_for_attempt": False,
        "retry_permitted": False,
        "predecessor_receipt_sha256": request.reservation_sha256,
        **request.model_dump(
            mode="python",
            exclude={"approved_plan_sha256", "activation_nonce", "dispatch_nonce"},
        ),
    }
    serialized = WorkflowDispatchClaimReceipt.model_construct(
        _fields_set=set(body),
        **body,
        receipt_sha256="0" * 64,
    ).model_dump(mode="json", by_alias=True, exclude={"receipt_sha256"})
    return WorkflowDispatchClaimReceipt.model_validate(
        {**body, "receipt_sha256": sha256(canonicalize(serialized)).hexdigest()}
    )


__all__ = ("build_claim_receipt",)
