"""Durable execution of regular report and retention daily jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never, final

from app.api.routes.cron import DailyOutcomeStatus
from app.domain.enums import JobKind, JobStatus

from .daily_jobs import (
    MAX_JOB_ATTEMPTS,
    DailyJobStore,
    JobClaim,
    JobClaimDisposition,
    JobCompletion,
)
from .daily_runtime import (
    RECONCILIATION_FAILURES,
    DailyJobOutcome,
)

if TYPE_CHECKING:
    from datetime import date

    from .coordinator import ReportCoordinator
    from .retention_coordinator import RetentionCoordinator


@final
class DailyJobExecutor:
    """Run one durable job kind without coupling its peer's progress."""

    def __init__(
        self,
        jobs: DailyJobStore,
        reports: ReportCoordinator,
        retention: RetentionCoordinator,
    ) -> None:
        """Bind the job store to report and retention coordinators."""
        self._jobs = jobs
        self._reports = reports
        self._retention = retention

    async def report_outcome(
        self,
        target_date: date,
        first_pending: date,
        can_continue: bool,
    ) -> DailyJobOutcome:
        """Run one report date after enforcing its predecessor."""
        if target_date < first_pending:
            return await self._correction_outcome(target_date)
        if not can_continue:
            return DailyJobOutcome(
                DailyOutcomeStatus.BLOCKED,
                "report_predecessor_failed",
            )
        claim = await self._jobs.claim(
            JobKind.DAILY_REPORT,
            target_date,
            await self._jobs.now(),
        )
        if claim.disposition is JobClaimDisposition.SUCCEEDED:
            return await self._correction_outcome(target_date)
        recovered = _recovered_claim(claim, "report")
        if recovered is not None:
            return recovered
        try:
            appended = await self._reports.reconcile(target_date)
        except RECONCILIATION_FAILURES:
            await self._finish_failure(claim, "report_reconciliation_failed")
            return DailyJobOutcome(
                DailyOutcomeStatus.FAILED,
                "report_reconciliation_failed",
            )
        await self._jobs.finish(
            claim,
            await self._jobs.now(),
            JobCompletion(
                status=JobStatus.SUCCEEDED,
                report_outcome={
                    "created": appended.created,
                    "report_id": str(appended.version.report_id),
                    "version_id": str(appended.version.version_id),
                    "revision": appended.version.revision,
                },
                retention_outcome=None,
                error_code=None,
            ),
        )
        return DailyJobOutcome(DailyOutcomeStatus.SUCCEEDED, None)

    async def _correction_outcome(self, target_date: date) -> DailyJobOutcome:
        try:
            _ = await self._reports.reconcile(target_date)
        except RECONCILIATION_FAILURES:
            return DailyJobOutcome(
                DailyOutcomeStatus.FAILED,
                "report_reconciliation_failed",
            )
        return DailyJobOutcome(DailyOutcomeStatus.SUCCEEDED, None)

    async def retention_outcome(
        self,
        target_date: date,
        first_pending: date,
        can_continue: bool,
    ) -> DailyJobOutcome:
        """Run one retention date after enforcing its predecessor."""
        if target_date < first_pending:
            return DailyJobOutcome(DailyOutcomeStatus.SKIPPED, None)
        if not can_continue:
            return DailyJobOutcome(
                DailyOutcomeStatus.BLOCKED,
                "retention_predecessor_failed",
            )
        observed_at = await self._jobs.now()
        claim = await self._jobs.claim(
            JobKind.RETENTION,
            target_date,
            observed_at,
        )
        recovered = _recovered_claim(claim, "retention")
        if recovered is not None:
            return recovered
        try:
            retained = await self._retention.reconcile(observed_at)
        except RECONCILIATION_FAILURES:
            await self._finish_failure(claim, "retention_reconciliation_failed")
            return DailyJobOutcome(
                DailyOutcomeStatus.FAILED,
                "retention_reconciliation_failed",
            )
        await self._jobs.finish(
            claim,
            await self._jobs.now(),
            JobCompletion(
                status=JobStatus.SUCCEEDED,
                report_outcome=None,
                retention_outcome={
                    "cleaned_source_count": retained.cleaned_source_count,
                    "expired_manifest_count": retained.expired_manifest_count,
                    "expired_tombstone_count": retained.expired_tombstone_count,
                },
                error_code=None,
            ),
        )
        return DailyJobOutcome(DailyOutcomeStatus.SUCCEEDED, None)

    async def _finish_failure(self, claim: JobClaim, error_code: str) -> None:
        status = (
            JobStatus.FAILED_RETRYABLE
            if claim.attempt < MAX_JOB_ATTEMPTS
            else JobStatus.FAILED_TERMINAL
        )
        await self._jobs.finish(
            claim,
            await self._jobs.now(),
            JobCompletion(
                status=status,
                report_outcome=None,
                retention_outcome=None,
                error_code=error_code,
            ),
        )


def _recovered_claim(
    claim: JobClaim,
    prefix: str,
) -> DailyJobOutcome | None:
    match claim.disposition:
        case JobClaimDisposition.RUN:
            return None
        case JobClaimDisposition.SUCCEEDED:
            return DailyJobOutcome(DailyOutcomeStatus.SKIPPED, None)
        case JobClaimDisposition.BUSY:
            return DailyJobOutcome(
                DailyOutcomeStatus.BLOCKED,
                f"{prefix}_job_busy",
            )
        case JobClaimDisposition.TERMINAL:
            return DailyJobOutcome(
                DailyOutcomeStatus.FAILED,
                f"{prefix}_job_terminal",
            )
        case _:
            assert_never(claim.disposition)
