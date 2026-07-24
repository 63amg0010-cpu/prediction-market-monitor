from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Self
from uuid import UUID

import pytest
from app.domain.enums import AuthorizationStatus, Country, SourcePlatform
from app.reporting.input_assembler import project_report_input
from app.reporting.input_policy import ReportAssemblyPolicy
from app.reporting.manifest import build_manifest
from app.reporting.sql_input_rows import (
    ReportInputQuery,
    ReportInputQueryRows,
    SourceSqlRow,
    load_report_input_rows,
)
from app.reporting.sql_input_statements import (
    DATABASE_CLOCK,
    PUBLICATIONS_FOR_WINDOWS,
    REPEATABLE_READ,
    REPORT_RECORDS,
    RUNS_FOR_WINDOWS,
    SLOTS_FOR_WINDOWS,
    SOURCES_FOR_SCOPE,
)
from app.reporting.windows import seoul_report_windows
from sqlalchemy.ext.asyncio import AsyncSession

from .factories import REPORT_DATE, manifest_payload

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from sqlalchemy.sql.base import Executable

type _RowValue = (
    UUID | AuthorizationStatus | Country | SourcePlatform | datetime | str | bool | None
)
type _Row = Mapping[str, _RowValue]
type _BindValue = str | datetime | list[UUID] | list[str]


@dataclass(frozen=True, slots=True)
class _MappingResult:
    values: tuple[_Row, ...]

    def mappings(self) -> Self:
        return self

    def __iter__(self) -> Iterator[_Row]:
        return iter(self.values)

    def one(self) -> _Row:
        assert len(self.values) == 1
        return self.values[0]


def _policy() -> ReportAssemblyPolicy:
    template = manifest_payload(())
    return ReportAssemblyPolicy(
        source_scope_version=template.source_scope_version,
        definitions=template.definitions,
        mappings=template.category_mappings,
        default_category="uncategorized",
        expected_sources=frozenset({(SourcePlatform.REDDIT, "r/Polymarket")}),
    )


def _rows(observed_at: datetime, source: SourceSqlRow) -> ReportInputQueryRows:
    return ReportInputQueryRows(
        report_date_seoul=REPORT_DATE,
        observed_at=observed_at,
        records=(),
        matches=(),
        sources=(source,),
        slots=(),
        runs=(),
        publications=(),
    )


def test_authorization_identity_uses_window_facts_not_execution_time() -> None:
    # Given: persisted authorization expires after the report window.
    window = seoul_report_windows(REPORT_DATE).primary
    source = SourceSqlRow(
        source_id=UUID(int=10),
        country=Country.US,
        platform=SourcePlatform.REDDIT,
        community="r/Polymarket",
        external_key="r/Polymarket",
        source_enabled=True,
        authorization_status=AuthorizationStatus.APPROVED,
        authorization_effective_at=window.start_utc - timedelta(days=1),
        authorization_expires_at=window.end_utc + timedelta(hours=1),
        authorization_revoked_at=None,
    )

    # When: identical historical rows are assembled before and after expiry.
    before = build_manifest(
        project_report_input(_policy(), _rows(window.end_utc, source))
    )
    later = build_manifest(
        project_report_input(
            _policy(),
            _rows(datetime(2026, 8, 1, tzinfo=UTC), source),
        )
    )
    revoked = source.model_copy(
        update={
            "authorization_status": AuthorizationStatus.REVOKED,
            "authorization_revoked_at": window.end_utc - timedelta(minutes=1),
        }
    )
    changed = build_manifest(
        project_report_input(_policy(), _rows(window.end_utc, revoked))
    )

    # Then: execution time is absent from identity, while persisted auth changes it.
    assert before.envelope.input_set_hash == later.envelope.input_set_hash
    assert changed.envelope.input_set_hash != before.envelope.input_set_hash


@pytest.mark.asyncio
async def test_sql_loader_uses_persisted_window_as_of_without_clock_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a real SQLAlchemy session boundary with deterministic empty input rows.
    windows = seoul_report_windows(REPORT_DATE)
    policy = _policy()
    analysis_version = policy.definitions.analysis_versions[0]
    query = ReportInputQuery(
        report_date_seoul=REPORT_DATE,
        source_scope_version=policy.source_scope_version,
        comparison_start=windows.comparison.start_utc,
        primary_end=windows.primary.end_utc,
        analysis_version=analysis_version,
        rule_set_versions=tuple(item.version for item in policy.definitions.rule_sets),
    )
    source: _Row = {
        "source_id": UUID(int=10),
        "country": Country.US,
        "platform": SourcePlatform.REDDIT,
        "community": "r/Polymarket",
        "external_key": "r/Polymarket",
        "source_enabled": True,
        "authorization_status": AuthorizationStatus.APPROVED,
        "authorization_effective_at": windows.comparison.start_utc,
        "authorization_expires_at": windows.primary.end_utc + timedelta(hours=1),
        "authorization_revoked_at": None,
    }
    executed: list[Executable] = []

    async def execute(
        statement: Executable,
        _parameters: Mapping[str, _BindValue] | None = None,
    ) -> _MappingResult:
        executed.append(statement)
        return _MappingResult((source,) if statement is SOURCES_FOR_SCOPE else ())

    # When: the production loader assembles one repeatable-read query set.
    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        rows = await load_report_input_rows(session, query)

    # Then: the stable report-window boundary replaces reconciliation wall time.
    assert rows.observed_at == windows.primary.end_utc
    assert DATABASE_CLOCK not in executed
    assert tuple(executed) == (
        REPEATABLE_READ,
        REPORT_RECORDS,
        SOURCES_FOR_SCOPE,
        SLOTS_FOR_WINDOWS,
        RUNS_FOR_WINDOWS,
        PUBLICATIONS_FOR_WINDOWS,
    )
