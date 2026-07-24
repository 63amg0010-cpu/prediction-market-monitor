"""Typed persistence ports for append-only daily reports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, final

import anyio
from anyio.lowlevel import checkpoint
from sqlalchemy import Date, bindparam, select, text

from app.db.manifest_models import ReportInputManifest as StoredManifest
from app.db.report_models import DailyReport, DailyReportVersion

from ._report_rows import (
    add_manifest_items,
    manifest_row,
    retained,
    stored,
    version_row,
)
from .manifest import ManifestIntegrityError, read_manifest
from .report_schema import DailyReportPayload
from .repository_types import (
    AppendReportOutcome,
    AppendReportRequest,
    StoredReportVersion,
    VersionAppendState,
)
from .reproduction import ManifestCorrupt, RetainedReport, reproduce_report

if TYPE_CHECKING:
    from datetime import date
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.session import DatabaseSessions


LOCK_REPORT_DATE = text(
    "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:report_date AS text), 0))"
).bindparams(bindparam("report_date", type_=Date()))
LOCK_DAILY_REPORT = (
    select(DailyReport)
    .where(DailyReport.report_date_seoul == bindparam("report_date"))
    .with_for_update()
)
LOAD_RETAINED_REPORT = (
    select(StoredManifest, DailyReportVersion)
    .join(DailyReportVersion, DailyReportVersion.manifest_id == StoredManifest.id)
    .where(StoredManifest.id == bindparam("manifest_id"))
)


class ReportRepository(Protocol):
    """Persistence boundary required by report reconciliation."""

    async def append_if_changed(
        self,
        request: AppendReportRequest,
    ) -> AppendReportOutcome:
        """Append after comparing identity with the current version atomically."""
        ...

    async def load_retained(self, manifest_id: UUID) -> RetainedReport | None:
        """Load only retained projection artifacts by manifest identity."""
        ...


@final
class InMemoryReportRepository:
    """Behavioral reference adapter with the same atomic append contract."""

    def __init__(self) -> None:
        """Initialize empty histories and their atomic identity lock."""
        self._lock: anyio.Lock = anyio.Lock()
        self._histories: dict[date, list[StoredReportVersion]] = {}

    async def append_if_changed(
        self,
        request: AppendReportRequest,
    ) -> AppendReportOutcome:
        """Serialize the identity check and append as one critical section."""
        async with self._lock:
            history = self._histories.setdefault(request.report_date_seoul, [])
            latest = history[-1] if history else None
            if (
                latest is not None
                and latest.report_schema_version == request.report_schema_version
                and latest.input_set_hash == request.input_set_hash
            ):
                return AppendReportOutcome(version=latest, created=False)
            version = StoredReportVersion(
                report_id=(
                    latest.report_id if latest is not None else request.report_id
                ),
                version_id=request.version_id,
                manifest_id=request.manifest_id,
                report_date_seoul=request.report_date_seoul,
                revision=1 if latest is None else latest.revision + 1,
                supersedes_version_id=(None if latest is None else latest.version_id),
                report_schema_version=request.report_schema_version,
                input_set_hash=request.input_set_hash,
                created_at=request.created_at,
                retain_until=request.retain_until,
                retained=request.retained,
            )
            history.append(version)
            return AppendReportOutcome(version=version, created=True)

    async def load_retained(self, manifest_id: UUID) -> RetainedReport | None:
        """Return a retained artifact without consulting any source entity."""
        await checkpoint()
        for history in self._histories.values():
            for version in history:
                if version.manifest_id == manifest_id:
                    return version.retained
        return None

    def history(self, report_date_seoul: date) -> tuple[StoredReportVersion, ...]:
        """Expose immutable history snapshots for adapter contract tests."""
        return tuple(self._histories.get(report_date_seoul, ()))


@final
class SqlAlchemyReportRepository:
    """Durable atomic report history and retained-only replay adapter."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        """Bind report persistence to one durable session owner."""
        self._sessions = sessions

    async def append_if_changed(
        self,
        request: AppendReportRequest,
    ) -> AppendReportOutcome:
        """Append one preassembled report in a serializable transaction."""
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            )
            return await self.append_in_session(session, request)

    async def append_in_session(
        self,
        session: AsyncSession,
        request: AppendReportRequest,
    ) -> AppendReportOutcome:
        """Lock and append inside an existing report-input transaction."""
        _ = await session.execute(
            LOCK_REPORT_DATE,
            {"report_date": request.report_date_seoul},
        )
        report = (
            await session.execute(
                LOCK_DAILY_REPORT,
                {"report_date": request.report_date_seoul},
            )
        ).scalar_one_or_none()
        return await _append_locked(session, report, request)

    async def load_retained(self, manifest_id: UUID) -> RetainedReport | None:
        """Load only retained manifest and report projection rows."""
        async with self._sessions.open() as session, session.begin():
            row = (
                (
                    await session.execute(
                        LOAD_RETAINED_REPORT,
                        {"manifest_id": manifest_id},
                    )
                )
                .tuples()
                .one_or_none()
            )
            return None if row is None else retained(*row)


async def _append_locked(
    session: AsyncSession,
    report: DailyReport | None,
    request: AppendReportRequest,
) -> AppendReportOutcome:
    latest = None
    if report is not None and report.latest_version_id is not None:
        latest = await session.get(DailyReportVersion, report.latest_version_id)
    if (
        latest is not None
        and latest.report_schema_version == request.report_schema_version
        and latest.input_set_hash == request.input_set_hash
    ):
        stored_manifest = await session.get(StoredManifest, latest.manifest_id)
        if stored_manifest is None:
            message = "retained_manifest_missing"
            raise RuntimeError(message)
        return AppendReportOutcome(
            version=stored(latest, retained(stored_manifest, latest)),
            created=False,
        )
    replay = reproduce_report(request.retained)
    if isinstance(replay, ManifestCorrupt):
        message = replay.reason
        raise ManifestIntegrityError(message)
    payload = read_manifest(request.retained.manifest)
    projected = DailyReportPayload.model_validate_json(request.retained.report_payload)
    report = report or DailyReport(
        id=request.report_id,
        report_date_seoul=request.report_date_seoul,
        latest_version_id=None,
        created_at=request.created_at,
    )
    revision = 1 if latest is None else latest.revision + 1
    version = version_row(
        request,
        projected,
        VersionAppendState(report.id, revision, latest),
    )
    manifest = manifest_row(request, payload, revision)
    session.add_all((report, version, manifest))
    add_manifest_items(session, payload, request.manifest_id)
    report.latest_version_id = version.id
    return AppendReportOutcome(
        version=stored(version, request.retained),
        created=True,
    )
