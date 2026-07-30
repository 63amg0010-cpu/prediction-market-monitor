"""Deterministic cadence receipt and replay comparison helpers."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING

from app.services.release.cadence_workflow_models import (
    CadenceWorkflowAttemptReceipt,
    CadenceWorkflowAttemptRequest,
)
from app.services.release.receipts import canonicalize

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime
    from uuid import UUID


def same_attempt(
    payload: CadenceWorkflowAttemptRequest,
    existing: Mapping[str, object],
    sources: tuple[Mapping[str, object], ...],
) -> bool:
    """Require a replay to match every retained request and source byte."""
    identity = (
        existing["cadence_epoch_id"],
        existing["schedule_kind"],
        existing["slot_key"],
        existing["workflow_mode"],
        existing["workflow_file"],
        existing["workflow_run_id"],
        existing["workflow_run_attempt"],
        existing["cadence_attempt"],
        existing["failed_predecessor_attempt_id"],
        existing["started_at"],
        existing["completed_at"],
    )
    expected = (
        payload.epoch_id,
        payload.schedule_kind,
        payload.slot_key,
        payload.workflow_mode,
        payload.workflow,
        payload.run_id,
        payload.run_attempt,
        payload.cadence_attempt,
        payload.failed_predecessor_attempt_id,
        payload.started_at,
        payload.completed_at,
    )
    actual_sources = {
        (
            item["source_id"],
            bool(item["succeeded"]),
            str(item["receipt_sha256"]),
        )
        for item in sources
    }
    expected_sources = {
        (
            item.source_id,
            item.status == "succeeded",
            item.receipt_sha256,
        )
        for item in payload.source_results
    }
    return identity == expected and actual_sources == expected_sources


def build_receipt(
    attempt_id: UUID,
    accepted: bool,
    reason: str,
    retry_permitted: bool,
    created_at: datetime,
) -> CadenceWorkflowAttemptReceipt:
    """Build the stable public-safe receipt without provider content."""
    body = {
        "schema": "cadence-workflow-attempt-receipt.v1",
        "attempt_id": str(attempt_id),
        "recorded": True,
        "cadence_accepted": accepted,
        "reason": reason,
        "retry_permitted": retry_permitted,
        "created_at_db": created_at.isoformat(),
    }
    digest = sha256(canonicalize(body)).hexdigest()
    return CadenceWorkflowAttemptReceipt(
        schema="cadence-workflow-attempt-receipt.v1",
        attempt_id=attempt_id,
        recorded=True,
        cadence_accepted=accepted,
        reason=reason,
        retry_permitted=retry_permitted,
        created_at_db=created_at,
        receipt_sha256=digest,
    )
