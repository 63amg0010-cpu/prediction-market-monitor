"""Database-time statements and deterministic freshness verification formulas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final

from sqlalchemy import DateTime, Select, func, select

from app.api.routes.verification import VerificationSourceResult
from app.db.verifier_models import VerificationCursor
from app.domain.enums import VerificationStatus

if TYPE_CHECKING:
    from uuid import UUID

VERIFICATION_INTERVAL: Final = timedelta(minutes=15)
MAX_FRESHNESS_SECONDS: Final = 3 * 60 * 60


@dataclass(frozen=True, slots=True)
class SourceVerificationFacts:
    """Persisted source facts captured by one consistent API snapshot."""

    source_id: UUID
    enabled: bool
    snapshot_published_at: datetime
    latest_successful_run_id: UUID | None
    latest_successful_run_finished_at: datetime | None
    visible_publication_manifest_id: UUID | None
    visible_publication_sequence: int | None
    publication_first_visible_at: datetime | None


def verification_clock_statement() -> Select[tuple[datetime]]:
    """Select the PostgreSQL wall clock used by verifier transactions."""
    return select(func.clock_timestamp(type_=DateTime(timezone=True)))


def locked_verification_cursor_statement(
    scope_version: str,
) -> Select[tuple[VerificationCursor]]:
    """Select one scope cursor under a PostgreSQL row lock."""
    return (
        select(VerificationCursor)
        .where(VerificationCursor.scope_version == scope_version)
        .with_for_update()
    )


def verification_slots(
    last_observed: datetime | None, target: datetime
) -> tuple[datetime, ...]:
    """Return every missing fifteen-minute slot through the target."""
    _require_aligned_utc(target)
    if last_observed is None:
        return (target,)
    _require_aligned_utc(last_observed)
    if last_observed >= target:
        return ()
    slots: list[datetime] = []
    slot = last_observed + VERIFICATION_INTERVAL
    while slot <= target:
        slots.append(slot)
        slot += VERIFICATION_INTERVAL
    return tuple(slots)


def derive_source_result(  # noqa: PLR0911 - exhaustive fail-closed S/C/P outcomes.
    facts: SourceVerificationFacts,
    expected_slot: datetime,
    action_started_at: datetime,
) -> VerificationSourceResult:
    """Derive S/C/P status exclusively from snapshot and action timestamps."""
    _require_aware_utc(expected_slot)
    _require_aware_utc(action_started_at)
    _require_aware_utc(facts.snapshot_published_at)
    scheduler_latency = int((action_started_at - expected_slot).total_seconds())
    if scheduler_latency < 0:
        error_code = "action_started_before_expected_slot"
        raise ValueError(error_code)
    finished_at = facts.latest_successful_run_finished_at
    if not facts.enabled:
        return _failed(
            facts.source_id, scheduler_latency, None, None, "source_disabled"
        )
    if facts.latest_successful_run_id is None or finished_at is None:
        return _failed(
            facts.source_id, scheduler_latency, None, None, "no_successful_run"
        )
    _require_aware_utc(finished_at)
    collection = int((facts.snapshot_published_at - finished_at).total_seconds())
    if collection < 0:
        return _failed(
            facts.source_id, scheduler_latency, None, None, "collection_clock_invalid"
        )
    if collection > MAX_FRESHNESS_SECONDS:
        return _failed(
            facts.source_id, scheduler_latency, collection, None, "collection_stale"
        )
    if (
        facts.visible_publication_manifest_id is None
        or facts.visible_publication_sequence is None
        or facts.publication_first_visible_at is None
    ):
        return _failed(
            facts.source_id, scheduler_latency, collection, None, "publication_missing"
        )
    _require_aware_utc(facts.publication_first_visible_at)
    publication = int(
        (facts.publication_first_visible_at - finished_at).total_seconds()
    )
    if publication < 0:
        return _failed(
            facts.source_id,
            scheduler_latency,
            collection,
            None,
            "publication_clock_invalid",
        )
    if publication > MAX_FRESHNESS_SECONDS:
        return _failed(
            facts.source_id,
            scheduler_latency,
            collection,
            publication,
            "publication_stale",
        )
    return VerificationSourceResult(
        source_id=facts.source_id,
        scheduler_latency_seconds=scheduler_latency,
        collection_recency_seconds=collection,
        publication_latency_seconds=publication,
        status=VerificationStatus.PASSED,
        failure_code=None,
    )


def _failed(
    source_id: UUID,
    scheduler: int,
    collection: int | None,
    publication: int | None,
    code: str,
) -> VerificationSourceResult:
    return VerificationSourceResult(
        source_id=source_id,
        scheduler_latency_seconds=scheduler,
        collection_recency_seconds=collection,
        publication_latency_seconds=publication,
        status=VerificationStatus.FAILED,
        failure_code=code,
    )


def _require_aligned_utc(value: datetime) -> None:
    _require_aware_utc(value)
    if value.minute % 15 or value.second or value.microsecond:
        error_code = "verification_slot_not_aligned"
        raise ValueError(error_code)


def _require_aware_utc(value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        error_code = "verification_timestamp_not_utc"
        raise ValueError(error_code)


__all__ = (
    "SourceVerificationFacts",
    "derive_source_result",
    "locked_verification_cursor_statement",
    "verification_clock_statement",
    "verification_slots",
)
