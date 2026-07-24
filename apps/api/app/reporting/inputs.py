"""Value-complete report input models."""

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, ClassVar, assert_never
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.domain.enums import (
    AnalysisState,
    Country,
    ReportRole,
    Sentiment,
    SourcePlatform,
)

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SafeCount = Annotated[int, Field(ge=0, lt=2**53)]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        error_code = "timestamp_timezone_missing"
        raise PydanticCustomError(error_code, "timezone is required")
    return value.astimezone(UTC)


def format_utc(value: datetime) -> str:
    """Format an aware timestamp in the report identity representation."""
    return _as_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


UtcTimestamp = Annotated[
    datetime,
    AfterValidator(_as_utc),
    PlainSerializer(format_utc, return_type=str),
]


class ImmutableReportModel(BaseModel):
    """Strict immutable base for retained report values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class EngagementSelectionState(StrEnum):
    """Whether a deterministic engagement observation was available."""

    SELECTED = "selected"
    UNAVAILABLE = "unavailable"


class AnalysisSnapshot(ImmutableReportModel):
    """Analysis values and explicit null provenance for one post version."""

    state: AnalysisState
    analysis_id: UUID | None
    output_hash: Sha256Hex | None
    prompt_version: str | None
    model_version: str | None
    schema_version: str | None
    analyzed_at: UtcTimestamp | None
    relevance: bool | None
    sentiment: Sentiment | None

    @model_validator(mode="after")
    def validate_state(self) -> "AnalysisSnapshot":
        """Bind valid values and missing-state nulls exhaustively."""
        provenance = (
            self.analysis_id,
            self.output_hash,
            self.prompt_version,
            self.model_version,
            self.schema_version,
            self.analyzed_at,
        )
        match self.state:
            case AnalysisState.VALID:
                if any(value is None for value in provenance) or self.relevance is None:
                    error_code = "valid_analysis_provenance_missing"
                    raise PydanticCustomError(
                        error_code,
                        "valid analysis requires provenance and relevance",
                    )
                if not self.relevance and self.sentiment is not None:
                    error_code = "irrelevant_sentiment_present"
                    raise PydanticCustomError(
                        error_code,
                        "irrelevant analysis cannot carry sentiment",
                    )
            case (
                AnalysisState.PENDING
                | AnalysisState.BLOCKED_CAPABILITY
                | AnalysisState.FAILED_RETRYABLE
                | AnalysisState.FAILED_TERMINAL
                | AnalysisState.INVALID_OUTPUT
            ):
                values = (*provenance, self.relevance, self.sentiment)
                if any(value is not None for value in values):
                    error_code = "missing_analysis_value_present"
                    raise PydanticCustomError(
                        error_code,
                        "non-valid analysis values must be null",
                    )
            case _:
                assert_never(self.state)
        return self


class EngagementSnapshot(ImmutableReportModel):
    """Latest engagement selection with independent nullable metrics."""

    selection_state: EngagementSelectionState
    observation_id: UUID | None
    engagement_hash: Sha256Hex | None
    observed_at: UtcTimestamp | None
    comments_count: SafeCount | None
    upvote_or_score: int | None = Field(ge=-(2**53) + 1, lt=2**53)

    @model_validator(mode="after")
    def validate_selection(self) -> "EngagementSnapshot":
        """Require provenance only for selected observations."""
        provenance = (self.observation_id, self.engagement_hash, self.observed_at)
        match self.selection_state:
            case EngagementSelectionState.SELECTED:
                if any(value is None for value in provenance):
                    error_code = "engagement_provenance_missing"
                    raise PydanticCustomError(
                        error_code,
                        "selected engagement requires provenance",
                    )
            case EngagementSelectionState.UNAVAILABLE:
                values = (*provenance, self.comments_count, self.upvote_or_score)
                if any(value is not None for value in values):
                    error_code = "unavailable_engagement_value_present"
                    raise PydanticCustomError(
                        error_code,
                        "unavailable engagement values must be null",
                    )
            case _:
                assert_never(self.selection_state)
        return self


class RuleMatchSnapshot(ImmutableReportModel):
    """One immutable keyword match and its effective category mapping."""

    match_id: UUID
    match_hash: Sha256Hex
    rule_id: str
    rule_set_id: str
    rule_set_version: str
    normalized_phrase: str
    match_present: bool
    mapped_category: str


class TopicMatchSnapshot(ImmutableReportModel):
    """One normalized analysis topic and its effective category mapping."""

    topic_key: str
    normalized_value: str
    analysis_schema_version: str
    mapped_category: str


class ReportRecord(ImmutableReportModel):
    """Body-free formula-effective record retained for one P or Q role."""

    ordinal: SafeCount
    role: ReportRole
    source_id: UUID
    country: Country
    platform: SourcePlatform
    community: str
    post_version_id: UUID
    post_content_hash: Sha256Hex
    published_at_utc: UtcTimestamp
    published_date_seoul: date
    source_publication_sequence: int = Field(gt=0, lt=2**53)
    source_publication_manifest_id: UUID
    source_publication_manifest_hash: Sha256Hex
    analysis: AnalysisSnapshot
    rule_matches: tuple[RuleMatchSnapshot, ...]
    topic_matches: tuple[TopicMatchSnapshot, ...]
    effective_categories: tuple[str, ...]
    engagement: EngagementSnapshot
