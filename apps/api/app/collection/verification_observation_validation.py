"""Exact persisted-snapshot validation for verifier observation payloads."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException

from .verification import derive_source_result

if TYPE_CHECKING:
    from datetime import datetime

    from app.api.routes.verification import (
        VerificationObservationPayload,
        VerificationSourceResult,
    )

    from .verification import SourceVerificationFacts
    from .verification_snapshot_identity import SnapshotEnvelope


def reject_duplicate_observation(existing_count: int) -> None:
    """Reject every repeated scope/slot before payload equivalence is considered."""
    if existing_count:
        raise HTTPException(status_code=409)


def reject_snapshot_reuse(snapshot_was_used: bool) -> None:
    """Reject every repeated consumption of one durable GET snapshot."""
    if snapshot_was_used:
        raise HTTPException(status_code=409)


def validated_source_results(
    snapshot: SnapshotEnvelope,
    facts: tuple[SourceVerificationFacts, ...],
    payload: VerificationObservationPayload,
    observed_at: datetime,
) -> tuple[VerificationSourceResult, ...]:
    """Recompute S/C/P and reject identity, time, or client-result changes."""
    observed_offset = observed_at.utcoffset()
    if (
        payload.scope_version != snapshot.evidence.scope_version
        or payload.snapshot_id != snapshot.snapshot_id
        or payload.snapshot_checksum != snapshot.checksum
        or observed_offset is None
        or observed_offset.total_seconds() != 0
        or snapshot.evidence.published_at > observed_at
        or payload.action_started_at < payload.expected_slot_utc
        or payload.action_started_at > snapshot.evidence.published_at
    ):
        raise HTTPException(status_code=409)
    try:
        expected = tuple(
            derive_source_result(
                item,
                payload.expected_slot_utc,
                payload.action_started_at,
            )
            for item in facts
        )
    except ValueError as error:
        raise HTTPException(status_code=409) from error
    supplied = {item.source_id: item for item in payload.source_results}
    derived = {item.source_id: item for item in expected}
    if supplied != derived:
        raise HTTPException(status_code=409)
    return expected


__all__ = (
    "reject_duplicate_observation",
    "reject_snapshot_reuse",
    "validated_source_results",
)
