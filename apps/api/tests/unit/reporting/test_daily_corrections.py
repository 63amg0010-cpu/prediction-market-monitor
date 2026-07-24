from datetime import date, timedelta
from typing import final

import pytest
from app.domain.enums import JobKind, ReportRole
from app.reporting.daily import SqlAlchemyDailyCronHandler
from app.reporting.daily_jobs import JobClaimDisposition
from app.reporting.manifest_schema import ReportInputManifest
from app.reporting.reconciliation import reconcile_report
from app.reporting.repository import InMemoryReportRepository
from app.reporting.repository_types import AppendReportOutcome
from app.reporting.windows import seoul_report_windows

from .integration_factories import late_correction_payloads, reconcile_request
from .test_daily import LATEST, JobsFake, RetentionFake


@final
class _ReportReconciler:
    def __init__(self) -> None:
        self.repository: InMemoryReportRepository = InMemoryReportRepository()
        self.payloads: dict[date, ReportInputManifest] = {}
        self.calls: list[date] = []
        self.outcomes: list[AppendReportOutcome] = []
        self._seed: int = 1

    async def reconcile(self, report_date: date) -> AppendReportOutcome:
        self.calls.append(report_date)
        outcome = await reconcile_report(
            self.repository,
            reconcile_request(self.payloads[report_date], self._seed),
        )
        self._seed += 1
        self.outcomes.append(outcome)
        return outcome


def _payload_for_date(
    payload: ReportInputManifest,
    report_date: date,
) -> ReportInputManifest:
    windows = seoul_report_windows(report_date)
    records = tuple(
        item.model_copy(
            update={
                "published_at_utc": (
                    windows.primary.start_utc
                    if item.role is ReportRole.PRIMARY
                    else windows.comparison.start_utc
                ),
                "published_date_seoul": (
                    windows.primary.date_seoul
                    if item.role is ReportRole.PRIMARY
                    else windows.comparison.date_seoul
                ),
            }
        )
        for item in payload.records
    )
    return payload.model_copy(
        update={
            "report_date_seoul": report_date,
            "windows": (windows.primary, windows.comparison),
            "records": records,
        }
    )


@pytest.mark.asyncio
async def test_scheduled_daily_corrects_trailing_seven_reports_once() -> None:
    # Given: seven succeeded reports and four successive late P/Q fact changes.
    dates = tuple(LATEST - timedelta(days=offset) for offset in range(6, -1, -1))
    affected = dates[-2:]
    stages = late_correction_payloads()
    reports = _ReportReconciler()
    for report_date in dates:
        reports.payloads[report_date] = _payload_for_date(stages[0], report_date)
        _ = await reports.reconcile(report_date)
    reports.calls.clear()
    reports.outcomes.clear()
    dispositions = {
        (JobKind.DAILY_REPORT, report_date): JobClaimDisposition.SUCCEEDED
        for report_date in dates
    }
    dispositions[(JobKind.RETENTION, LATEST)] = JobClaimDisposition.SUCCEEDED
    jobs = JobsFake(
        {JobKind.DAILY_REPORT: LATEST, JobKind.RETENTION: LATEST},
        dispositions,
    )
    handler = SqlAlchemyDailyCronHandler(jobs, reports, RetentionFake())

    # When: production scheduled composition sees each changed stage, then a retry.
    for stage in stages[1:]:
        for report_date in affected:
            reports.payloads[report_date] = _payload_for_date(stage, report_date)
        response = await handler.run_daily()
        assert tuple(item.target_date_seoul for item in response.outcomes) == dates
    _ = await handler.run_daily()

    # Then: every scan is ascending/bounded and each changed identity appends once.
    assert all(
        tuple(reports.calls[offset : offset + 7]) == dates
        for offset in range(0, len(reports.calls), 7)
    )
    assert (
        tuple(len(reports.repository.history(item)) for item in dates[:-2]) == (1,) * 5
    )
    assert tuple(len(reports.repository.history(item)) for item in affected) == (5, 5)
    assert all(outcome.created is False for outcome in reports.outcomes[-7:])
