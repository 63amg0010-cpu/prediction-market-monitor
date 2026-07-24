"""Durable claims and outcomes for bounded daily PostgreSQL jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum, unique
from hashlib import sha256
from secrets import token_bytes
from typing import TYPE_CHECKING, Protocol, final
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.operations_models import ScheduledJobRun
from app.domain.enums import JobKind, JobStatus

from .sql_input_rows import database_time

if TYPE_CHECKING:
    from datetime import date, datetime

    from app.db.session import DatabaseSessions
    from app.domain.types import JsonValue

STALE_JOB_AFTER = timedelta(minutes=15)
MAX_JOB_ATTEMPTS = 3


@unique
class JobClaimDisposition(StrEnum):
    """Exclusive state returned while claiming one idempotent job."""

    RUN = "run"
    SUCCEEDED = "succeeded"
    BUSY = "busy"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class JobClaim:
    """Claimed job identity and opaque compare-and-set lease."""

    disposition: JobClaimDisposition
    job_id: UUID
    attempt: int
    lease_hash: bytes | None


@dataclass(frozen=True, slots=True)
class JobCompletion:
    """Redacted durable values written when a claimed job finishes."""

    status: JobStatus
    report_outcome: JsonValue | None
    retention_outcome: JsonValue | None
    error_code: str | None


class DailyJobStore(Protocol):
    """Persistence contract for clocks, catch-up cursors, and job leases."""

    async def now(self) -> datetime:
        """Read the database clock used for all daily decisions."""
        ...

    async def latest_succeeded_date(
        self,
        kind: JobKind,
        through_date: date,
    ) -> date | None:
        """Read the last successful target date for one job kind."""
        ...

    async def claim(
        self,
        kind: JobKind,
        target_date: date,
        observed_at: datetime,
    ) -> JobClaim:
        """Create, recover, or reject one idempotent job claim."""
        ...

    async def claim_reconciliation(
        self,
        observed_at: datetime,
    ) -> JobClaim | None:
        """Claim the oldest queued administrator reconciliation trigger."""
        ...

    async def finish(
        self,
        claim: JobClaim,
        observed_at: datetime,
        completion: JobCompletion,
    ) -> None:
        """Complete only the row still holding the supplied lease."""
        ...


@final
class SqlAlchemyDailyJobStore:
    """PostgreSQL daily-job store with stale-lease recovery."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        """Bind all job operations to one shared database owner."""
        self._sessions = sessions

    async def now(self) -> datetime:
        """Return the authoritative PostgreSQL clock."""
        async with self._sessions.open() as session, session.begin():
            return await database_time(session)

    async def latest_succeeded_date(
        self,
        kind: JobKind,
        through_date: date,
    ) -> date | None:
        """Return the bounded kind-specific catch-up cursor."""
        statement = select(func.max(ScheduledJobRun.target_date_seoul)).where(
            ScheduledJobRun.kind == kind,
            ScheduledJobRun.status == JobStatus.SUCCEEDED,
            ScheduledJobRun.target_date_seoul <= through_date,
        )
        async with self._sessions.open() as session, session.begin():
            return await session.scalar(statement)

    async def claim(
        self,
        kind: JobKind,
        target_date: date,
        observed_at: datetime,
    ) -> JobClaim:
        """Claim with an idempotency key and recover stale attempts."""
        idempotency_key = f"daily-job/v1/{kind.value}/{target_date.isoformat()}"
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(
                insert(ScheduledJobRun)
                .values(
                    id=uuid4(),
                    kind=kind,
                    target_date_seoul=target_date,
                    idempotency_key=idempotency_key,
                    status=JobStatus.QUEUED,
                    attempt=1,
                    available_at=observed_at,
                    created_at=observed_at,
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )
            job = await session.scalar(
                select(ScheduledJobRun)
                .where(ScheduledJobRun.idempotency_key == idempotency_key)
                .with_for_update()
            )
            if job is None:
                message = "daily_job_claim_missing"
                raise RuntimeError(message)
            return self._claim_locked(job, observed_at)

    async def claim_reconciliation(
        self,
        observed_at: datetime,
    ) -> JobClaim | None:
        """Claim one due administrator trigger with skip-locked ordering."""
        eligible_statuses = (
            JobStatus.QUEUED,
            JobStatus.RUNNING,
            JobStatus.FAILED_RETRYABLE,
        )
        async with self._sessions.open() as session, session.begin():
            job = await session.scalar(
                select(ScheduledJobRun)
                .where(
                    ScheduledJobRun.kind == JobKind.RECONCILIATION,
                    ScheduledJobRun.status.in_(eligible_statuses),
                    ScheduledJobRun.available_at <= observed_at,
                )
                .order_by(ScheduledJobRun.created_at, ScheduledJobRun.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            return self._claim_locked(job, observed_at)

    async def finish(
        self,
        claim: JobClaim,
        observed_at: datetime,
        completion: JobCompletion,
    ) -> None:
        """Persist a redacted outcome under lease compare-and-set."""
        if claim.lease_hash is None:
            message = "daily_job_finish_without_lease"
            raise RuntimeError(message)
        statement = (
            update(ScheduledJobRun)
            .where(
                ScheduledJobRun.id == claim.job_id,
                ScheduledJobRun.status == JobStatus.RUNNING,
                ScheduledJobRun.lease_hash == claim.lease_hash,
            )
            .values(
                status=completion.status,
                lease_hash=None,
                heartbeat_at=observed_at,
                finished_at=observed_at,
                report_outcome=completion.report_outcome,
                retention_outcome=completion.retention_outcome,
                error_code=completion.error_code,
            )
            .returning(ScheduledJobRun.id)
        )
        async with self._sessions.open() as session, session.begin():
            updated = (await session.execute(statement)).scalar_one_or_none()
            if updated is None:
                message = "daily_job_finish_conflict"
                raise RuntimeError(message)

    def _claim_locked(
        self,
        job: ScheduledJobRun,
        observed_at: datetime,
    ) -> JobClaim:
        if job.status is JobStatus.SUCCEEDED:
            return JobClaim(JobClaimDisposition.SUCCEEDED, job.id, job.attempt, None)
        if job.status is JobStatus.FAILED_TERMINAL:
            return JobClaim(JobClaimDisposition.TERMINAL, job.id, job.attempt, None)
        if job.status is JobStatus.RUNNING:
            activity_at = job.heartbeat_at or job.started_at
            if activity_at is not None and observed_at - activity_at < STALE_JOB_AFTER:
                return JobClaim(JobClaimDisposition.BUSY, job.id, job.attempt, None)
        elif (
            job.status is JobStatus.FAILED_RETRYABLE and job.available_at > observed_at
        ):
            return JobClaim(JobClaimDisposition.BUSY, job.id, job.attempt, None)
        attempt = job.attempt + int(job.status is not JobStatus.QUEUED)
        if attempt > MAX_JOB_ATTEMPTS:
            job.status = JobStatus.FAILED_TERMINAL
            job.finished_at = observed_at
            job.error_code = "daily_job_attempts_exhausted"
            return JobClaim(JobClaimDisposition.TERMINAL, job.id, job.attempt, None)
        lease_hash = sha256(token_bytes(32)).digest()
        job.status = JobStatus.RUNNING
        job.attempt = attempt
        job.available_at = observed_at
        job.lease_hash = lease_hash
        job.started_at = observed_at
        job.heartbeat_at = observed_at
        job.finished_at = None
        job.report_outcome = None
        job.retention_outcome = None
        job.error_code = None
        return JobClaim(JobClaimDisposition.RUN, job.id, attempt, lease_hash)
