"""Bounded coordination for one raw-retention and expiry pass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, final

from .retention import cleanup_source, expire_retained_reports
from .retention_types import CleanupRequest

if TYPE_CHECKING:
    from datetime import datetime

    from .retention_sql import SqlAlchemyRetentionRepository

RETENTION_SOURCE_BATCH = 100


@dataclass(frozen=True, slots=True)
class RetentionPassOutcome:
    """Count-only result safe for durable operational reporting."""

    cleaned_source_count: int
    expired_manifest_count: int
    expired_tombstone_count: int


class RetentionCoordinator(Protocol):
    """Run one bounded fail-closed retention pass."""

    async def reconcile(self, observed_at: datetime) -> RetentionPassOutcome:
        """Clean eligible sources and expire retained report graphs."""
        ...


@final
class SqlAlchemyRetentionCoordinator:
    """Coordinate restrictive SQL cleanup in dependency order."""

    def __init__(self, repository: SqlAlchemyRetentionRepository) -> None:
        """Bind one durable retention repository."""
        self._repository = repository

    async def reconcile(self, observed_at: datetime) -> RetentionPassOutcome:
        """Process at most one reviewed source batch plus report expiry."""
        source_ids = await self._repository.eligible_source_ids(
            observed_at,
            RETENTION_SOURCE_BATCH,
        )
        cleaned = 0
        for source_id in source_ids:
            outcome = await cleanup_source(
                self._repository,
                CleanupRequest(source_entity_id=source_id, observed_at=observed_at),
            )
            cleaned += int(outcome.deleted)
        expired = await expire_retained_reports(self._repository, observed_at)
        return RetentionPassOutcome(
            cleaned_source_count=cleaned,
            expired_manifest_count=len(expired.deleted_manifest_ids),
            expired_tombstone_count=len(expired.deleted_tombstone_ids),
        )
