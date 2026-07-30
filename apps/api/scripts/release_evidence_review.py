"""Protected dual-review validation with live-plan byte binding."""

# ruff: noqa: EM101, PLR2004, TC003

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping

from pydantic import ValidationError

from scripts.release_evidence_contracts import (
    EvidenceHoldError,
    ProtectedReviewRecord,
    ReviewBindings,
    ReviewRecordAccess,
)


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def validate_review_record(
    document: Mapping[str, object],
    *,
    access: ReviewRecordAccess,
    live_plan_path: str,
    live_plan_bytes: bytes,
    expected_sha: str,
) -> ReviewBindings:
    """Derive approval bindings from one owner-only schema-closed record."""
    if access.committed or access.symlinked or access.world_readable:
        raise EvidenceHoldError("review_record_not_owner_only")
    try:
        record = ProtectedReviewRecord.model_validate(document)
    except ValidationError as error:
        raise EvidenceHoldError("review_record_schema_rejected") from error
    if len(expected_sha) != 40 or any(
        character not in "0123456789abcdef" for character in expected_sha
    ):
        raise EvidenceHoldError("reviewed_sha_invalid")

    live_digest = hashlib.sha256(live_plan_bytes).hexdigest()
    lanes = (record.review.momus, record.review.independent)
    plan_matches = (
        record.plan_path == live_plan_path
        and record.plan_bytes == len(live_plan_bytes)
        and hmac.compare_digest(record.plan_sha256, live_digest)
    )
    lane_matches = all(
        lane.target == record.plan_path
        and lane.round_id == record.review_round_id
        and lane.plan_bytes == record.plan_bytes
        and hmac.compare_digest(lane.plan_sha256, record.plan_sha256)
        for lane in lanes
    )
    launch_ids = (lanes[0].launch_id, lanes[1].launch_id)
    if not plan_matches:
        raise EvidenceHoldError("live_plan_binding_mismatch")
    if not lane_matches:
        raise EvidenceHoldError("approval_lane_binding_mismatch")
    if launch_ids[0] == launch_ids[1]:
        raise EvidenceHoldError("approval_launch_ids_not_distinct")
    return ReviewBindings(
        reviewed_sha=expected_sha,
        approved_plan_sha256=record.plan_sha256,
        approval_round_id=_digest_text(record.review_round_id),
        approval_launch_sha256s=(
            _digest_text(launch_ids[0]),
            _digest_text(launch_ids[1]),
        ),
    )


__all__ = ("validate_review_record",)
