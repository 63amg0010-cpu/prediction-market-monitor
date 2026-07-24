"""Atomic verifier cursor advancement and snapshot-bound observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, text

from app.api.routes.verification import (
    ObservationAccepted,
    VerificationSourceResult,
)
from app.db.verifier_models import (
    VerificationCursor,
    VerificationObservation,
    VerificationSnapshotUse,
)
from app.domain.enums import VerificationStatus

from .verification import verification_slots
from .verification_observation_validation import (
    reject_duplicate_observation,
    reject_snapshot_reuse,
    validated_source_results,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.routes.verification import VerificationObservationPayload

    from .verification import SourceVerificationFacts
    from .verification_snapshot_identity import SnapshotEnvelope

_LOCK_SCOPE = text("SELECT pg_advisory_xact_lock(hashtextextended(:scope_version, 0))")


@dataclass(frozen=True, slots=True)
class _SlotContext:
    session: AsyncSession
    snapshot: SnapshotEnvelope
    facts: tuple[SourceVerificationFacts, ...]
    payload: VerificationObservationPayload
    target_results: tuple[VerificationSourceResult, ...]
    observed_at: datetime


async def record_observations(
    session: AsyncSession,
    snapshot: SnapshotEnvelope,
    facts: tuple[SourceVerificationFacts, ...],
    payload: VerificationObservationPayload,
    observed_at: datetime,
) -> ObservationAccepted:
    """Persist missing slots and one exact snapshot-bound target observation."""
    existing = await _existing_observations(session, payload)
    reject_duplicate_observation(len(existing))
    reject_snapshot_reuse(await _snapshot_was_used(session, payload.snapshot_id))
    target_results = validated_source_results(snapshot, facts, payload, observed_at)
    cursor = await _locked_cursor(session, payload.scope_version, observed_at)
    try:
        slots = verification_slots(
            cursor.last_materialized_slot_utc, payload.expected_slot_utc
        )
    except ValueError as error:
        raise HTTPException(status_code=422) from error
    if not slots or slots[-1] != payload.expected_slot_utc:
        raise HTTPException(status_code=409)
    context = _SlotContext(
        session,
        snapshot,
        facts,
        payload,
        target_results,
        observed_at,
    )
    for slot in slots:
        _persist_slot(context, slot, missing=slot != payload.expected_slot_utc)
    session.add(
        VerificationSnapshotUse(
            id=uuid4(),
            snapshot_id=snapshot.snapshot_id,
            scope_version=payload.scope_version,
            expected_slot_utc=payload.expected_slot_utc,
            action_started_at=payload.action_started_at,
            consumed_at=observed_at,
        )
    )
    cursor.last_materialized_slot_utc = payload.expected_slot_utc
    cursor.updated_at = observed_at
    return ObservationAccepted(
        expected_slot_utc=payload.expected_slot_utc,
        accepted_source_count=len(facts),
    )


async def _locked_cursor(
    session: AsyncSession, scope_version: str, now: datetime
) -> VerificationCursor:
    _ = await session.execute(_LOCK_SCOPE, {"scope_version": scope_version})
    cursor = (
        await session.execute(
            select(VerificationCursor)
            .where(VerificationCursor.scope_version == scope_version)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if cursor is None:
        cursor = VerificationCursor(
            id=uuid4(),
            scope_version=scope_version,
            last_materialized_slot_utc=None,
            updated_at=now,
        )
        session.add(cursor)
    return cursor


async def _existing_observations(
    session: AsyncSession, payload: VerificationObservationPayload
) -> tuple[VerificationObservation, ...]:
    return tuple(
        (
            await session.execute(
                select(VerificationObservation)
                .where(
                    VerificationObservation.scope_version == payload.scope_version,
                    VerificationObservation.expected_slot_utc
                    == payload.expected_slot_utc,
                )
                .order_by(VerificationObservation.source_id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def _snapshot_was_used(session: AsyncSession, snapshot_id: UUID) -> bool:
    durable_use = (
        await session.execute(
            select(VerificationSnapshotUse.id)
            .where(VerificationSnapshotUse.snapshot_id == snapshot_id)
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if durable_use is not None:
        return True
    legacy_observation = (
        await session.execute(
            select(VerificationObservation.id)
            .where(VerificationObservation.snapshot_id == snapshot_id)
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    return legacy_observation is not None


def _persist_slot(context: _SlotContext, slot: datetime, *, missing: bool) -> None:
    target = {item.source_id: item for item in context.target_results}
    for facts in context.facts:
        result = _missing_result(
            facts.source_id, slot, context.payload.action_started_at
        )
        if not missing:
            result = target[facts.source_id]
        context.session.add(
            VerificationObservation(
                id=uuid4(),
                scope_version=context.payload.scope_version,
                expected_slot_utc=slot,
                source_id=facts.source_id,
                snapshot_id=context.snapshot.snapshot_id,
                snapshot_published_at=context.snapshot.evidence.published_at,
                latest_successful_run_id=facts.latest_successful_run_id,
                visible_publication_manifest_id=(facts.visible_publication_manifest_id),
                visible_publication_sequence=facts.visible_publication_sequence,
                action_started_at=context.payload.action_started_at,
                scheduler_latency_seconds=result.scheduler_latency_seconds,
                collection_recency_seconds=result.collection_recency_seconds,
                publication_latency_seconds=result.publication_latency_seconds,
                status=result.status,
                failure_code=result.failure_code,
                snapshot_checksum=context.snapshot.checksum,
                observed_at=context.observed_at,
            )
        )


def _missing_result(
    source_id: UUID, slot: datetime, action_started_at: datetime
) -> VerificationSourceResult:
    latency = int((action_started_at - slot).total_seconds())
    if latency < 0:
        raise HTTPException(status_code=409)
    return VerificationSourceResult(
        source_id=source_id,
        scheduler_latency_seconds=latency,
        collection_recency_seconds=None,
        publication_latency_seconds=None,
        status=VerificationStatus.MISSING,
        failure_code="verification_slot_missing",
    )


__all__ = ("record_observations",)
