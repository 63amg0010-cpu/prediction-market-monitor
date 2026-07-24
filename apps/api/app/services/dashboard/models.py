"""Immutable API projections that preserve operational uncertainty."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime  # noqa: TC003 - Pydantic resolves at runtime.
from decimal import Decimal  # noqa: TC003 - Pydantic resolves this at runtime.
from enum import StrEnum, unique
from typing import TYPE_CHECKING, ClassVar
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field

from app.domain.enums import (  # noqa: TC001 - Pydantic resolves these at runtime.
    AnalysisState,
    Country,
    ReportStatus,
    Sentiment,
)
from app.reporting.coverage import (
    SourceCoverage,  # noqa: TC001 - Pydantic runtime field.
)
from app.reporting.inputs import Sha256Hex  # noqa: TC001 - Pydantic runtime field.
from app.reporting.report_schema import (  # noqa: TC001 - Pydantic runtime fields.
    Highlight,
    RisingKeyword,
)

if TYPE_CHECKING:
    from app.core.principals import PrincipalId, Scope


@unique
class OutcomeStatus(StrEnum):
    """Dashboard outcomes that must never collapse to a success boolean."""

    SUCCESS = "success"
    PENDING = "pending"
    BLOCKED = "blocked"
    PARTIAL = "partial"
    ERROR = "error"
    UNKNOWN = "unknown"


@unique
class DatabaseStatus(StrEnum):
    """Database states safe to expose without topology details."""

    OK = "ok"
    UNAVAILABLE = "unavailable"


@unique
class ReproductionStatus(StrEnum):
    """Whether retained bytes passed deterministic report reproduction."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class AuthorizedService:
    """Verified service identity returned by a scope authorizer."""

    principal_id: PrincipalId
    scopes: frozenset[Scope]


class _Projection(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class MentionSummary(_Projection):
    """Current and comparison mention counts with nullable rate semantics."""

    current_count: int = Field(ge=0)
    previous_count: int = Field(ge=0)
    delta: int
    delta_rate: Decimal | None
    status: OutcomeStatus


class AnalysisSummary(_Projection):
    """Analysis coverage and sentiment counts without imputing missing work."""

    candidate_count: int = Field(ge=0)
    valid_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    coverage: Decimal | None
    positive_count: int = Field(ge=0)
    neutral_count: int = Field(ge=0)
    negative_count: int = Field(ge=0)
    unknown_sentiment_count: int = Field(ge=0)
    status: OutcomeStatus


class EngagementSummary(_Projection):
    """Nullable engagement sums paired with known and unknown populations."""

    comments_sum: int | None
    comments_known_count: int = Field(ge=0)
    comments_unknown_count: int = Field(ge=0)
    score_sum: int | None
    score_known_count: int = Field(ge=0)
    score_unknown_count: int = Field(ge=0)
    status: OutcomeStatus


class OperationsSummary(_Projection):
    """Collection and worker status anchors displayed by the dashboard."""

    last_complete_collection_at: datetime | None
    last_analysis_at: datetime | None
    pending_analysis_count: int = Field(ge=0)
    blocked_analysis_count: int = Field(ge=0)
    collection_status: OutcomeStatus
    analysis_status: OutcomeStatus


class SourceStatus(_Projection):
    """One reviewed source's latest collection and publication evidence."""

    source_id: UUID
    display_name: str
    country: Country
    enabled: bool
    status: OutcomeStatus
    latest_successful_run_at: datetime | None
    visible_publication_sequence: int | None = Field(default=None, ge=1)
    failure_code: str | None = None
    retry_eligible: bool
    retry_block_reason: str | None


class DashboardResponse(_Projection):
    """Consistent dashboard snapshot containing metrics and evidence states."""

    generated_at: datetime
    mentions: MentionSummary
    analysis: AnalysisSummary
    engagement: EngagementSummary
    operations: OperationsSummary
    sources: tuple[SourceStatus, ...]


class PostItem(_Projection):
    """Author-free current post projection with its external original link."""

    id: UUID
    source_id: UUID
    source_name: str
    country: Country
    title: str
    original_url: AnyHttpUrl
    published_at: datetime
    analysis_state: AnalysisState
    relevance: bool | None
    sentiment: Sentiment | None
    comments_count: int | None = Field(default=None, ge=0)
    score: int | None = None
    engagement_status: OutcomeStatus


class PageInfo(_Projection):
    """Bounded page metadata shared by posts and reports."""

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total_items: int = Field(ge=0)
    has_next: bool


class PostPage(_Projection):
    """One stable page of post projections."""

    items: tuple[PostItem, ...]
    page: PageInfo


class ReportItem(_Projection):
    """Latest immutable report revision without binary persistence payloads."""

    id: UUID
    report_date_seoul: date
    revision: int = Field(ge=1)
    status: ReportStatus
    candidate_count: int = Field(ge=0)
    relevant_count: int = Field(ge=0)
    pending_count: int = Field(ge=0)
    analysis_coverage: Decimal | None
    comments_sum: int | None
    score_sum: int | None
    highlights: tuple[Highlight, ...]
    rising_keywords: tuple[RisingKeyword, ...]
    source_coverage: tuple[SourceCoverage, ...]
    manifest_id: UUID
    input_set_hash: Sha256Hex
    manifest_payload_sha256: Sha256Hex
    report_payload_sha256: Sha256Hex
    reproduction_status: ReproductionStatus
    created_at: datetime


class ReportPage(_Projection):
    """One stable page of latest report revisions."""

    items: tuple[ReportItem, ...]
    page: PageInfo
