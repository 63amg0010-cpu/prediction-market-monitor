"""Immutable values shared by durable report persistence components."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime
    from uuid import UUID

    from app.db.report_models import DailyReportVersion

    from .reproduction import RetainedReport


@dataclass(frozen=True, slots=True)
class AppendReportRequest:
    """One fully materialized report version proposed for atomic append."""

    report_id: UUID
    version_id: UUID
    manifest_id: UUID
    report_date_seoul: date
    report_schema_version: str
    input_set_hash: str
    created_at: datetime
    retain_until: datetime
    retained: RetainedReport


@dataclass(frozen=True, slots=True)
class StoredReportVersion:
    """One immutable version in a report date's append-only history."""

    report_id: UUID
    version_id: UUID
    manifest_id: UUID
    report_date_seoul: date
    revision: int
    supersedes_version_id: UUID | None
    report_schema_version: str
    input_set_hash: str
    created_at: datetime
    retain_until: datetime
    retained: RetainedReport


@dataclass(frozen=True, slots=True)
class AppendReportOutcome:
    """Atomic append result, including an idempotent reuse signal."""

    version: StoredReportVersion
    created: bool


@dataclass(frozen=True, slots=True)
class VersionAppendState:
    """Locked report identity needed to materialize its next version row."""

    report_id: UUID
    revision: int
    latest: DailyReportVersion | None
