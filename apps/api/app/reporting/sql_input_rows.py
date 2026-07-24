"""Typed PostgreSQL rows and loader for deterministic report input assembly."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic resolves at runtime.
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID  # noqa: TC003 - Pydantic resolves at runtime.

from pydantic import BaseModel, ConfigDict

from app.domain.enums import (  # noqa: TC001 - Pydantic resolves at runtime.
    AnalysisState,
    AuthorizationStatus,
    Country,
    QueueStatus,
    RunStatus,
    Sentiment,
    SourcePlatform,
)
from app.services.configuration.canonical import canonical_sha256

from .manifest_schema import (  # noqa: TC001 - Pydantic resolves at runtime.
    AnalysisVersionTuple,
)
from .sql_input_statements import (
    DATABASE_CLOCK,
    PUBLICATIONS_FOR_WINDOWS,
    REPEATABLE_READ,
    REPORT_MATCHES,
    REPORT_RECORDS,
    RUNS_FOR_WINDOWS,
    SLOTS_FOR_WINDOWS,
    SOURCES_FOR_SCOPE,
)

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession


class _SqlRow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class PublicationFingerprint(BaseModel):
    """Stable source publication identity used after source-row deletion."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    run_id: UUID
    source_id: UUID
    terminal_page_commit_id: UUID
    sequence: int
    final_chain_hash: str
    post_set_hash: str
    distinct_post_version_count: int
    zero_post: bool
    committed_at: datetime


def publication_hash(fingerprint: PublicationFingerprint) -> str:
    """Hash every immutable source publication field canonically."""
    return canonical_sha256(fingerprint)


class RecordSqlRow(_SqlRow):
    """Visible current post version with selected report evidence."""

    source_id: UUID
    country: Country
    platform: SourcePlatform
    community: str
    post_version_id: UUID
    post_content_hash: str
    published_at_utc: datetime
    analysis_id: UUID | None
    analysis_state: AnalysisState | None
    output_hash: str | None
    prompt_version: str | None
    model_version: str | None
    schema_version: str | None
    analyzed_at: datetime | None
    relevance: bool | None
    sentiment: Sentiment | None
    topics: tuple[str, ...] | None
    queue_status: QueueStatus | None
    engagement_observation_id: UUID | None
    engagement_hash: str | None
    engagement_observed_at: datetime | None
    comments_count: int | None
    upvote_or_score: int | None
    publication_id: UUID
    publication_run_id: UUID
    publication_terminal_page_commit_id: UUID
    publication_sequence: int
    publication_final_chain_hash: str
    publication_post_set_hash: str
    publication_distinct_post_version_count: int
    publication_zero_post: bool
    publication_committed_at: datetime

    def publication(self) -> PublicationFingerprint:
        """Return the immutable publication fields used for provenance."""
        return PublicationFingerprint(
            id=self.publication_id,
            run_id=self.publication_run_id,
            source_id=self.source_id,
            terminal_page_commit_id=self.publication_terminal_page_commit_id,
            sequence=self.publication_sequence,
            final_chain_hash=self.publication_final_chain_hash,
            post_set_hash=self.publication_post_set_hash,
            distinct_post_version_count=(self.publication_distinct_post_version_count),
            zero_post=self.publication_zero_post,
            committed_at=self.publication_committed_at,
        )


class MatchSqlRow(_SqlRow):
    """Configured immutable rule result for one visible post version."""

    post_version_id: UUID
    match_id: UUID
    match_hash: str
    rule_set_version: str
    rule_set_hash: str
    normalized_phrase: str
    match_present: bool
    stored_category: str
    rule_category: str


class SourceSqlRow(_SqlRow):
    """Reviewed source with its active authorization decision."""

    source_id: UUID
    country: Country
    platform: SourcePlatform
    community: str
    external_key: str
    source_enabled: bool
    authorization_status: AuthorizationStatus | None
    authorization_effective_at: datetime | None
    authorization_expires_at: datetime | None
    authorization_revoked_at: datetime | None


class SlotSqlRow(_SqlRow):
    """Materialized collection due slot inside P or Q."""

    due_slot_utc: datetime


class RunSqlRow(_SqlRow):
    """Latest attempt for one source and due-slot command."""

    source_id: UUID
    due_slot_utc: datetime
    status: RunStatus
    attempt: int
    started_at: datetime | None
    finished_at: datetime | None


class PublicationSqlRow(_SqlRow):
    """Successful publication for a run scheduled inside P or Q."""

    id: UUID
    run_id: UUID
    source_id: UUID
    terminal_page_commit_id: UUID
    sequence: int
    final_chain_hash: str
    post_set_hash: str
    distinct_post_version_count: int
    zero_post: bool
    committed_at: datetime
    due_slot_utc: datetime

    def fingerprint(self) -> PublicationFingerprint:
        """Exclude scheduling metadata from publication identity."""
        return PublicationFingerprint.model_validate(
            self.model_dump(exclude={"due_slot_utc"})
        )


class _DatabaseClockRow(_SqlRow):
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ReportInputQuery:
    """All binds needed to read one exact report input snapshot."""

    report_date_seoul: date
    source_scope_version: str
    comparison_start: datetime
    primary_end: datetime
    analysis_version: AnalysisVersionTuple
    rule_set_versions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReportInputQueryRows:
    """Typed rows selected from one repeatable-read database snapshot."""

    report_date_seoul: date
    observed_at: datetime
    records: tuple[RecordSqlRow, ...]
    matches: tuple[MatchSqlRow, ...]
    sources: tuple[SourceSqlRow, ...]
    slots: tuple[SlotSqlRow, ...]
    runs: tuple[RunSqlRow, ...]
    publications: tuple[PublicationSqlRow, ...]


async def load_report_input_rows(
    session: AsyncSession,
    query: ReportInputQuery,
) -> ReportInputQueryRows:
    """Select every P/Q input before any report row is written."""
    _ = await session.execute(REPEATABLE_READ)
    parameters = {
        "scope_version": query.source_scope_version,
        "comparison_start": query.comparison_start,
        "primary_end": query.primary_end,
        "prompt_version": query.analysis_version.prompt_version,
        "model_version": query.analysis_version.model_version,
        "schema_version": query.analysis_version.schema_version,
    }
    records = tuple(
        RecordSqlRow.model_validate(row)
        for row in (await session.execute(REPORT_RECORDS, parameters)).mappings()
    )
    post_version_ids = [item.post_version_id for item in records]
    matches = (
        tuple(
            MatchSqlRow.model_validate(row)
            for row in (
                await session.execute(
                    REPORT_MATCHES,
                    {
                        "post_version_ids": post_version_ids,
                        "rule_set_versions": list(query.rule_set_versions),
                    },
                )
            ).mappings()
        )
        if post_version_ids and query.rule_set_versions
        else ()
    )
    sources = tuple(
        SourceSqlRow.model_validate(row)
        for row in (await session.execute(SOURCES_FOR_SCOPE, parameters)).mappings()
    )
    slots = tuple(
        SlotSqlRow.model_validate(row)
        for row in (await session.execute(SLOTS_FOR_WINDOWS, parameters)).mappings()
    )
    runs = tuple(
        RunSqlRow.model_validate(row)
        for row in (await session.execute(RUNS_FOR_WINDOWS, parameters)).mappings()
    )
    publications = tuple(
        PublicationSqlRow.model_validate(row)
        for row in (
            await session.execute(PUBLICATIONS_FOR_WINDOWS, parameters)
        ).mappings()
    )
    return ReportInputQueryRows(
        report_date_seoul=query.report_date_seoul,
        observed_at=query.primary_end,
        records=records,
        matches=matches,
        sources=sources,
        slots=slots,
        runs=runs,
        publications=publications,
    )


async def database_time(session: AsyncSession) -> datetime:
    """Read one aware PostgreSQL clock value for report metadata and policy."""
    return _DatabaseClockRow.model_validate(
        (await session.execute(DATABASE_CLOCK)).mappings().one()
    ).observed_at
