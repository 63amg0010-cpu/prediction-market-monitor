"""Atomic SQL coordination for report input selection and durable append."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, final
from uuid import uuid4

from .reconciliation import ReconcileRequest, append_report_request
from .repository import SqlAlchemyReportRepository
from .sql_input_rows import database_time

if TYPE_CHECKING:
    from datetime import date

    from app.db.session import DatabaseSessions

    from .input_assembler import SqlAlchemyReportInputAssembler
    from .repository_types import AppendReportOutcome


class ReportCoordinator(Protocol):
    """Boundary for reconciling one report date against durable P/Q facts."""

    async def reconcile(self, report_date: date) -> AppendReportOutcome:
        """Append exactly one changed revision or reuse the current revision."""
        ...


@final
class SqlAlchemyReportCoordinator:
    """Keep input selection, manifest items, and report append in one transaction."""

    def __init__(
        self,
        sessions: DatabaseSessions,
        assembler: SqlAlchemyReportInputAssembler,
    ) -> None:
        """Bind the shared session owner and its reviewed input assembler."""
        self._sessions = sessions
        self._assembler = assembler
        self._repository = SqlAlchemyReportRepository(sessions)

    async def reconcile(self, report_date: date) -> AppendReportOutcome:
        """Read P/Q and append its changed projection atomically."""
        async with self._sessions.open() as session, session.begin():
            payload = await self._assembler.assemble_in_session(session, report_date)
            created_at = await database_time(session)
            request = append_report_request(
                ReconcileRequest(
                    payload=payload,
                    created_at=created_at,
                    report_id=uuid4(),
                    version_id=uuid4(),
                    manifest_id=uuid4(),
                )
            )
            return await self._repository.append_in_session(session, request)
