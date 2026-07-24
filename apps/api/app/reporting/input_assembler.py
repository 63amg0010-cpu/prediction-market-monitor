"""Durable repeatable-read assembly of value-complete P/Q manifests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, final

from .input_coverage import source_coverage
from .input_policy import ReportAssemblyError, ReportAssemblyPolicy
from .input_records import report_records
from .manifest_schema import CategoryMappingSnapshot, ReportInputManifest
from .sql_input_rows import (
    ReportInputQuery,
    ReportInputQueryRows,
    load_report_input_rows,
)
from .windows import seoul_report_windows

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.session import DatabaseSessions

    from .inputs import ReportRecord


class ReportInputAssembler(Protocol):
    """Boundary for assembling one deterministic report-date input."""

    async def assemble(self, report_date: date) -> ReportInputManifest:
        """Read and validate one complete P/Q snapshot."""
        ...


@final
class SqlAlchemyReportInputAssembler:
    """PostgreSQL input assembler sharing a transaction with report writes."""

    def __init__(
        self,
        sessions: DatabaseSessions,
        policy: ReportAssemblyPolicy,
    ) -> None:
        """Bind one reviewed policy to the durable session owner."""
        self._sessions = sessions
        self._policy = policy

    async def assemble(self, report_date: date) -> ReportInputManifest:
        """Read a standalone repeatable-read snapshot for one report date."""
        async with self._sessions.open() as session, session.begin():
            return await self.assemble_in_session(session, report_date)

    async def assemble_in_session(
        self,
        session: AsyncSession,
        report_date: date,
    ) -> ReportInputManifest:
        """Read within the caller's transaction so append remains atomic."""
        versions = self._policy.definitions.analysis_versions
        if len(versions) != 1:
            reason = "report_analysis_version_count_invalid"
            raise ReportAssemblyError(reason)
        windows = seoul_report_windows(report_date)
        rows = await load_report_input_rows(
            session,
            ReportInputQuery(
                report_date_seoul=report_date,
                source_scope_version=self._policy.source_scope_version,
                comparison_start=windows.comparison.start_utc,
                primary_end=windows.primary.end_utc,
                analysis_version=versions[0],
                rule_set_versions=tuple(
                    item.version for item in self._policy.definitions.rule_sets
                ),
            ),
        )
        return project_report_input(self._policy, rows)


def project_report_input(
    policy: ReportAssemblyPolicy,
    rows: ReportInputQueryRows,
) -> ReportInputManifest:
    """Validate typed SQL rows and project the canonical manifest value."""
    records = report_records(policy, rows)
    mappings = _effective_mappings(policy, records)
    windows = seoul_report_windows(rows.report_date_seoul)
    return ReportInputManifest(
        schema="report-input-manifest/v1",
        report_date_seoul=rows.report_date_seoul,
        windows=(windows.primary, windows.comparison),
        source_scope_version=policy.source_scope_version,
        definitions=policy.definitions,
        category_mappings=mappings,
        records=records,
        source_coverage=source_coverage(policy, rows, records),
    )


def _effective_mappings(
    policy: ReportAssemblyPolicy,
    records: tuple[ReportRecord, ...],
) -> tuple[CategoryMappingSnapshot, ...]:
    selected = {
        (
            item.input_kind,
            item.rule_or_topic_key,
            item.version,
            item.normalized_value,
        ): item
        for item in policy.mappings
    }
    for record in records:
        for topic in record.topic_matches:
            mapping = policy.topic_mapping(
                topic.normalized_value,
                topic.analysis_schema_version,
            )
            key = (
                mapping.input_kind,
                mapping.rule_or_topic_key,
                mapping.version,
                mapping.normalized_value,
            )
            selected[key] = mapping
    return tuple(selected.values())
