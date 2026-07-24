"""Administrator-requested bounded daily reconciliation execution."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, final

from app.api.routes.cron import (
    DailyCronResponse,
    DailyOutcome,
    DailyOutcomeStatus,
)
from app.domain.enums import JobStatus

from .daily_jobs import MAX_JOB_ATTEMPTS, DailyJobStore, JobClaim, JobCompletion
from .daily_runtime import (
    MAX_CATCH_UP_DAYS,
    RECONCILIATION_FAILURES,
    DailyJobOutcome,
    catch_up_dates,
)

if TYPE_CHECKING:
    from datetime import date, datetime

    from app.domain.types import JsonValue

    from .coordinator import ReportCoordinator
    from .retention_coordinator import RetentionCoordinator


@final
class RequestedReconciliationRunner:
    """Reproject seven report dates and retain once for an admin trigger."""

    def __init__(
        self,
        jobs: DailyJobStore,
        reports: ReportCoordinator,
        retention: RetentionCoordinator,
    ) -> None:
        """Bind one requested reconciliation to its durable dependencies."""
        self._jobs = jobs
        self._reports = reports
        self._retention = retention

    async def run(
        self,
        started_at: datetime,
        latest_complete_date: date,
        claim: JobClaim,
    ) -> DailyCronResponse:
        """Execute and durably finish one claimed administrator trigger."""
        first = latest_complete_date - timedelta(days=MAX_CATCH_UP_DAYS - 1)
        target_dates = catch_up_dates(first, latest_complete_date)
        outcomes: list[DailyOutcome] = []
        report_successes = 0
        retention_summary: dict[str, JsonValue] | None = None
        failed = False
        for target_date in target_dates:
            report = await self._forced_report(target_date)
            report_successes += int(report.status is DailyOutcomeStatus.SUCCEEDED)
            if target_date == latest_complete_date:
                retention, retention_summary = await self._forced_retention(started_at)
            else:
                retention = DailyJobOutcome(DailyOutcomeStatus.SKIPPED, None)
            failed = failed or report.status is DailyOutcomeStatus.FAILED
            failed = failed or retention.status is DailyOutcomeStatus.FAILED
            outcomes.append(
                DailyOutcome(
                    target_date_seoul=target_date,
                    report=report.status,
                    retention=retention.status,
                    error_codes=tuple(
                        code
                        for code in (report.error_code, retention.error_code)
                        if code is not None
                    ),
                )
            )
        completion_status = (
            JobStatus.SUCCEEDED
            if not failed
            else (
                JobStatus.FAILED_RETRYABLE
                if claim.attempt < MAX_JOB_ATTEMPTS
                else JobStatus.FAILED_TERMINAL
            )
        )
        await self._jobs.finish(
            claim,
            await self._jobs.now(),
            JobCompletion(
                status=completion_status,
                report_outcome={
                    "attempted_date_count": len(target_dates),
                    "successful_date_count": report_successes,
                },
                retention_outcome=retention_summary,
                error_code="daily_reconciliation_failed" if failed else None,
            ),
        )
        return DailyCronResponse(
            started_at=started_at,
            finished_at=await self._jobs.now(),
            outcomes=tuple(outcomes),
        )

    async def _forced_report(self, target_date: date) -> DailyJobOutcome:
        try:
            _ = await self._reports.reconcile(target_date)
        except RECONCILIATION_FAILURES:
            return DailyJobOutcome(
                DailyOutcomeStatus.FAILED,
                "report_reconciliation_failed",
            )
        return DailyJobOutcome(DailyOutcomeStatus.SUCCEEDED, None)

    async def _forced_retention(
        self,
        observed_at: datetime,
    ) -> tuple[DailyJobOutcome, dict[str, JsonValue] | None]:
        try:
            retained = await self._retention.reconcile(observed_at)
        except RECONCILIATION_FAILURES:
            return (
                DailyJobOutcome(
                    DailyOutcomeStatus.FAILED,
                    "retention_reconciliation_failed",
                ),
                None,
            )
        return (
            DailyJobOutcome(DailyOutcomeStatus.SUCCEEDED, None),
            {
                "cleaned_source_count": retained.cleaned_source_count,
                "expired_manifest_count": retained.expired_manifest_count,
                "expired_tombstone_count": retained.expired_tombstone_count,
            },
        )
