"""Bounded independent daily report and retention reconciliation."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, final

from app.api.routes.cron import DailyCronResponse, DailyOutcome
from app.domain.enums import JobKind

from .daily_execution import DailyJobExecutor
from .daily_jobs import DailyJobStore, JobClaimDisposition
from .daily_reconciliation import RequestedReconciliationRunner
from .daily_runtime import MAX_CATCH_UP_DAYS, catch_up_dates
from .windows import SEOUL

if TYPE_CHECKING:
    from datetime import date

    from .coordinator import ReportCoordinator
    from .retention_coordinator import RetentionCoordinator


@final
class SqlAlchemyDailyCronHandler:
    """Reconcile up to seven ascending Seoul dates using durable jobs."""

    def __init__(
        self,
        jobs: DailyJobStore,
        reports: ReportCoordinator,
        retention: RetentionCoordinator,
    ) -> None:
        """Bind durable jobs to independent report and retention workers."""
        self._jobs = jobs
        self._executor = DailyJobExecutor(jobs, reports, retention)
        self._requested = RequestedReconciliationRunner(jobs, reports, retention)

    async def run_daily(self) -> DailyCronResponse:
        """Run bounded catch-up from the last successful date per job kind."""
        started_at = await self._jobs.now()
        latest_complete_date = started_at.astimezone(SEOUL).date() - timedelta(days=1)
        reconciliation = await self._jobs.claim_reconciliation(started_at)
        if (
            reconciliation is not None
            and reconciliation.disposition is JobClaimDisposition.RUN
        ):
            return await self._requested.run(
                started_at,
                latest_complete_date,
                reconciliation,
            )
        report_last = await self._jobs.latest_succeeded_date(
            JobKind.DAILY_REPORT,
            latest_complete_date,
        )
        retention_last = await self._jobs.latest_succeeded_date(
            JobKind.RETENTION,
            latest_complete_date,
        )
        report_next = _next_date(report_last, latest_complete_date)
        retention_next = _next_date(retention_last, latest_complete_date)
        first_target = min(report_next, retention_next)
        if report_last == latest_complete_date:
            first_target = min(
                first_target,
                latest_complete_date - timedelta(days=MAX_CATCH_UP_DAYS - 1),
            )
        target_dates = catch_up_dates(first_target, latest_complete_date)
        report_can_continue = True
        retention_can_continue = True
        outcomes: list[DailyOutcome] = []
        for target_date in target_dates:
            report = await self._executor.report_outcome(
                target_date,
                report_next,
                report_can_continue,
            )
            retention = await self._executor.retention_outcome(
                target_date,
                retention_next,
                retention_can_continue,
            )
            report_can_continue = report_can_continue and report.allows_successor
            retention_can_continue = (
                retention_can_continue and retention.allows_successor
            )
            error_codes = tuple(
                dict.fromkeys(
                    code
                    for code in (report.error_code, retention.error_code)
                    if code is not None
                )
            )
            outcomes.append(
                DailyOutcome(
                    target_date_seoul=target_date,
                    report=report.status,
                    retention=retention.status,
                    error_codes=error_codes,
                )
            )
        return DailyCronResponse(
            started_at=started_at,
            finished_at=await self._jobs.now(),
            outcomes=tuple(outcomes),
        )


def _next_date(last_succeeded: date | None, latest_complete: date) -> date:
    if last_succeeded is None:
        return latest_complete
    return min(last_succeeded + timedelta(days=1), latest_complete)
