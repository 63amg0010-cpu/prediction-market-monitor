"""Append-only daily report correction reconciliation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from .formula import project_report
from .manifest_schema import ReportInputManifest
from .repository import ReportRepository
from .repository_types import AppendReportOutcome, AppendReportRequest
from .reproduction import RetainedReport

REPORT_RETENTION_DAYS = 180
TRAILING_CORRECTION_DAYS = 7


@dataclass(frozen=True, slots=True)
class ReconcileRequest:
    """Deterministic inputs and proposed identities for one reconciliation."""

    payload: ReportInputManifest
    created_at: datetime
    report_id: UUID
    version_id: UUID
    manifest_id: UUID


@dataclass(frozen=True, slots=True)
class CorrectionTargets:
    """Affected report dates inside the bounded correction window."""

    report_dates: tuple[date, ...]
    outside_window: bool


async def reconcile_report(
    repository: ReportRepository,
    request: ReconcileRequest,
) -> AppendReportOutcome:
    """Project retained values and atomically append only a changed version."""
    return await repository.append_if_changed(append_report_request(request))


def append_report_request(request: ReconcileRequest) -> AppendReportRequest:
    """Project one manifest into the immutable atomic append request."""
    build = project_report(request.payload)
    retained = RetainedReport(
        manifest=build.manifest.envelope,
        report_payload=build.canonical_bytes,
        report_payload_sha256=build.payload_sha256,
    )
    return AppendReportRequest(
        report_id=request.report_id,
        version_id=request.version_id,
        manifest_id=request.manifest_id,
        report_date_seoul=build.payload.report_date_seoul,
        report_schema_version=build.payload.schema_name,
        input_set_hash=build.payload.input_set_hash,
        created_at=request.created_at,
        retain_until=request.created_at + timedelta(days=REPORT_RETENTION_DAYS),
        retained=retained,
    )


def correction_targets(
    changed_date_seoul: date,
    latest_report_date_seoul: date,
) -> CorrectionTargets:
    """Map a late fact to D and D+1 within the latest seven report dates."""
    first_retained = latest_report_date_seoul - timedelta(
        days=TRAILING_CORRECTION_DAYS - 1
    )
    affected = (changed_date_seoul, changed_date_seoul + timedelta(days=1))
    selected = tuple(
        report_date
        for report_date in affected
        if first_retained <= report_date <= latest_report_date_seoul
    )
    return CorrectionTargets(
        report_dates=selected,
        outside_window=not selected,
    )
