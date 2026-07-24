from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from app.api.routes.cron import DailyOutcomeStatus
from app.domain.enums import JobKind, JobStatus, ManifestCodec
from app.reporting.daily import SqlAlchemyDailyCronHandler
from app.reporting.daily_jobs import (
    JobClaim,
    JobClaimDisposition,
    JobCompletion,
)
from app.reporting.input_policy import ReportAssemblyError
from app.reporting.manifest import ManifestEnvelope
from app.reporting.repository_types import (
    AppendReportOutcome,
    StoredReportVersion,
)
from app.reporting.reproduction import RetainedReport
from app.reporting.retention_coordinator import RetentionPassOutcome

NOW = datetime(2026, 7, 22, 3, tzinfo=UTC)
LATEST = date(2026, 7, 21)


class JobsFake:
    def __init__(
        self,
        latest: dict[JobKind, date | None],
        dispositions: dict[tuple[JobKind, date], JobClaimDisposition] | None = None,
        reconciliation: JobClaim | None = None,
    ) -> None:
        self.latest: dict[JobKind, date | None] = latest
        self.dispositions: dict[tuple[JobKind, date], JobClaimDisposition] = (
            dispositions or {}
        )
        self.claimed: list[tuple[JobKind, date]] = []
        self.finished: list[tuple[JobClaim, JobCompletion]] = []
        self.reconciliation: JobClaim | None = reconciliation

    async def now(self) -> datetime:
        return NOW

    async def latest_succeeded_date(
        self,
        kind: JobKind,
        through_date: date,
    ) -> date | None:
        assert through_date == LATEST
        return self.latest[kind]

    async def claim(
        self,
        kind: JobKind,
        target_date: date,
        observed_at: datetime,
    ) -> JobClaim:
        assert observed_at == NOW
        self.claimed.append((kind, target_date))
        disposition = self.dispositions.get(
            (kind, target_date),
            JobClaimDisposition.RUN,
        )
        return JobClaim(
            disposition=disposition,
            job_id=uuid4(),
            attempt=1,
            lease_hash=b"l" * 32 if disposition is JobClaimDisposition.RUN else None,
        )

    async def claim_reconciliation(
        self,
        observed_at: datetime,
    ) -> JobClaim | None:
        assert observed_at == NOW
        return self.reconciliation

    async def finish(
        self,
        claim: JobClaim,
        observed_at: datetime,
        completion: JobCompletion,
    ) -> None:
        assert observed_at == NOW
        self.finished.append((claim, completion))


class _Reports:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail: bool = fail
        self.dates: list[date] = []

    async def reconcile(self, report_date: date) -> AppendReportOutcome:
        self.dates.append(report_date)
        if self.fail:
            reason = "sensitive_internal_reason"
            raise ReportAssemblyError(reason)
        report_id = uuid4()
        version_id = uuid4()
        manifest_id = uuid4()
        retained = RetainedReport(
            manifest=ManifestEnvelope(
                codec=ManifestCodec.GZIP,
                compressed_payload=b"manifest",
                uncompressed_byte_length=8,
                manifest_payload_sha256="a" * 64,
                input_set_hash="b" * 64,
            ),
            report_payload=b"report",
            report_payload_sha256="c" * 64,
        )
        return AppendReportOutcome(
            version=StoredReportVersion(
                report_id=report_id,
                version_id=version_id,
                manifest_id=manifest_id,
                report_date_seoul=report_date,
                revision=1,
                supersedes_version_id=None,
                report_schema_version="daily-report/v1",
                input_set_hash="b" * 64,
                created_at=NOW,
                retain_until=NOW + timedelta(days=180),
                retained=retained,
            ),
            created=True,
        )


class RetentionFake:
    def __init__(self) -> None:
        self.observed: list[datetime] = []

    async def reconcile(self, observed_at: datetime) -> RetentionPassOutcome:
        self.observed.append(observed_at)
        return RetentionPassOutcome(2, 3, 5)


@pytest.mark.asyncio
async def test_daily_first_run_processes_only_latest_completed_seoul_date() -> None:
    jobs = JobsFake({JobKind.DAILY_REPORT: None, JobKind.RETENTION: None})
    reports = _Reports()
    retention = RetentionFake()

    response = await SqlAlchemyDailyCronHandler(jobs, reports, retention).run_daily()

    assert tuple(item.target_date_seoul for item in response.outcomes) == (LATEST,)
    assert response.outcomes[0].report is DailyOutcomeStatus.SUCCEEDED
    assert response.outcomes[0].retention is DailyOutcomeStatus.SUCCEEDED
    assert reports.dates == [LATEST]
    assert retention.observed == [NOW]
    assert [item.status for _, item in jobs.finished] == [
        JobStatus.SUCCEEDED,
        JobStatus.SUCCEEDED,
    ]


@pytest.mark.asyncio
async def test_daily_catch_up_is_ascending_and_bounded_to_seven_dates() -> None:
    report_last = LATEST - timedelta(days=10)
    retention_last = LATEST - timedelta(days=3)
    jobs = JobsFake(
        {
            JobKind.DAILY_REPORT: report_last,
            JobKind.RETENTION: retention_last,
        }
    )
    reports = _Reports()
    retention = RetentionFake()

    response = await SqlAlchemyDailyCronHandler(jobs, reports, retention).run_daily()

    expected = tuple(report_last + timedelta(days=offset) for offset in range(1, 8))
    assert tuple(item.target_date_seoul for item in response.outcomes) == expected
    assert len(response.outcomes) == 7
    assert reports.dates == list(expected)
    assert retention.observed == []
    assert all(
        item.retention is DailyOutcomeStatus.SKIPPED for item in response.outcomes
    )


@pytest.mark.asyncio
async def test_report_failure_blocks_only_report_successors_and_redacts_reason() -> (
    None
):
    last = LATEST - timedelta(days=3)
    jobs = JobsFake({JobKind.DAILY_REPORT: last, JobKind.RETENTION: last})
    reports = _Reports(fail=True)
    retention = RetentionFake()

    response = await SqlAlchemyDailyCronHandler(jobs, reports, retention).run_daily()

    assert [item.report for item in response.outcomes] == [
        DailyOutcomeStatus.FAILED,
        DailyOutcomeStatus.BLOCKED,
        DailyOutcomeStatus.BLOCKED,
    ]
    assert all(
        item.retention is DailyOutcomeStatus.SUCCEEDED for item in response.outcomes
    )
    assert reports.dates == [last + timedelta(days=1)]
    assert len(retention.observed) == 3
    assert "sensitive_internal_reason" not in response.model_dump_json()
    failed = next(
        completion
        for _, completion in jobs.finished
        if completion.status is JobStatus.FAILED_RETRYABLE
    )
    assert failed.error_code == "report_reconciliation_failed"


@pytest.mark.asyncio
async def test_admin_reconciliation_reprojects_seven_dates_and_retains_once() -> None:
    reconciliation = JobClaim(
        disposition=JobClaimDisposition.RUN,
        job_id=uuid4(),
        attempt=1,
        lease_hash=b"r" * 32,
    )
    jobs = JobsFake(
        {JobKind.DAILY_REPORT: LATEST, JobKind.RETENTION: LATEST},
        reconciliation=reconciliation,
    )
    reports = _Reports()
    retention = RetentionFake()

    response = await SqlAlchemyDailyCronHandler(jobs, reports, retention).run_daily()

    expected = [LATEST - timedelta(days=offset) for offset in range(6, -1, -1)]
    assert reports.dates == expected
    assert len(response.outcomes) == 7
    assert [item.retention for item in response.outcomes[:-1]] == [
        DailyOutcomeStatus.SKIPPED
    ] * 6
    assert response.outcomes[-1].retention is DailyOutcomeStatus.SUCCEEDED
    assert retention.observed == [NOW]
    assert jobs.finished[-1][0] == reconciliation
    assert jobs.finished[-1][1].status is JobStatus.SUCCEEDED
