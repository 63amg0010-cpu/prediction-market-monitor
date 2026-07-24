"""Typed parsing of PostgreSQL dashboard result rows."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves at runtime.
from typing import ClassVar, Final, assert_never
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from pydantic import AnyHttpUrl, BaseModel, ConfigDict

from app.domain.enums import (
    AnalysisState,
    Country,
    QueueStatus,
    RunStatus,
    Sentiment,
)

from .models import (
    OutcomeStatus,
    PostItem,
    SourceStatus,
)

SOURCE_STALE_SECONDS: Final = 3 * 60 * 60
RETRYABLE_SOURCE_STATUSES: Final = frozenset(
    {RunStatus.FAILED_RETRYABLE, RunStatus.STALE_ABANDONED}
)
RETRY_BLOCK_REASONS: Final[dict[RunStatus | None, str]] = {
    None: "no_collection_attempt",
    RunStatus.CREATED: "collection_in_progress",
    RunStatus.RUNNING: "collection_in_progress",
    RunStatus.SUCCEEDED: "collection_succeeded",
    RunStatus.SKIPPED_POLICY: "skipped_policy",
    RunStatus.SKIPPED_QUOTA: "skipped_quota",
    RunStatus.FAILED_TERMINAL: "collection_terminal",
}


class _Row(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class CountRow(_Row):
    """Count result for a bounded page query."""

    total_items: int


class PostRow(_Row):
    """Post query row before API uncertainty projection."""

    id: UUID
    source_id: UUID
    source_name: str
    country: Country
    title: str
    original_url: AnyHttpUrl
    published_at: datetime
    stored_analysis_state: AnalysisState | None
    relevance: bool | None
    sentiment: Sentiment | None
    queue_status: QueueStatus | None
    comments_count: int | None
    score: int | None

    def projection(self) -> PostItem:
        """Project persisted and queue state into one author-free post."""
        return PostItem(
            id=self.id,
            source_id=self.source_id,
            source_name=self.source_name,
            country=self.country,
            title=self.title,
            original_url=self.original_url,
            published_at=self.published_at,
            analysis_state=_analysis_state(
                self.stored_analysis_state, self.queue_status
            ),
            relevance=self.relevance,
            sentiment=self.sentiment,
            comments_count=self.comments_count,
            score=self.score,
            engagement_status=_engagement_status(self.comments_count, self.score),
        )


class MetricRow(_Row):
    """Aggregate dashboard metric row."""

    current_count: int
    previous_count: int
    valid_count: int
    blocked_count: int
    positive_count: int
    neutral_count: int
    negative_count: int
    comments_sum: int | None
    comments_known_count: int
    score_sum: int | None
    score_known_count: int


class OperationRow(_Row):
    """Operational time and queue-count anchors."""

    generated_at: datetime
    last_complete_collection_at: datetime | None
    last_analysis_at: datetime | None
    pending_analysis_count: int
    blocked_analysis_count: int


class SourceRow(_Row):
    """Per-source collection and visible-publication evidence row."""

    source_id: UUID
    display_name: str
    country: Country
    enabled: bool
    latest_attempt_status: RunStatus | None
    latest_attempt_finished_at: datetime | None
    latest_successful_run_at: datetime | None
    visible_publication_sequence: int | None
    failure_code: str | None

    def projection(self, generated_at: datetime) -> SourceStatus:
        """Derive a redacted source status without fabricating freshness."""
        status, failure_code = _source_outcome(self, generated_at)
        retry_eligible, retry_block_reason = _retry_policy(self)
        return SourceStatus(
            source_id=self.source_id,
            display_name=self.display_name,
            country=self.country,
            enabled=self.enabled,
            status=status,
            latest_successful_run_at=self.latest_successful_run_at,
            visible_publication_sequence=self.visible_publication_sequence,
            failure_code=failure_code,
            retry_eligible=retry_eligible,
            retry_block_reason=retry_block_reason,
        )


def _analysis_state(
    stored: AnalysisState | None, queue: QueueStatus | None
) -> AnalysisState:
    if stored is not None:
        return stored
    match queue:
        case None | QueueStatus.PENDING | QueueStatus.LEASED | QueueStatus.SUCCEEDED:
            return AnalysisState.PENDING
        case QueueStatus.BLOCKED_CAPABILITY:
            return AnalysisState.BLOCKED_CAPABILITY
        case QueueStatus.FAILED_RETRYABLE:
            return AnalysisState.FAILED_RETRYABLE
        case QueueStatus.FAILED_TERMINAL:
            return AnalysisState.FAILED_TERMINAL
        case _:
            assert_never(queue)


def _source_outcome(
    row: SourceRow,
    generated_at: datetime,
) -> tuple[OutcomeStatus, str | None]:
    if not row.enabled:
        return OutcomeStatus.BLOCKED, "source_disabled"
    match row.latest_attempt_status:
        case None:
            return OutcomeStatus.UNKNOWN, "no_collection_attempt"
        case RunStatus.CREATED | RunStatus.RUNNING:
            return OutcomeStatus.PENDING, None
        case RunStatus.SUCCEEDED:
            return _successful_source_outcome(row, generated_at)
        case RunStatus.SKIPPED_POLICY | RunStatus.SKIPPED_QUOTA as status:
            default_code = (
                "skipped_policy"
                if status is RunStatus.SKIPPED_POLICY
                else "skipped_quota"
            )
            return OutcomeStatus.BLOCKED, row.failure_code or default_code
        case (
            RunStatus.FAILED_RETRYABLE
            | RunStatus.STALE_ABANDONED
            | RunStatus.FAILED_TERMINAL
        ) as status:
            default_code = (
                "collection_terminal"
                if status is RunStatus.FAILED_TERMINAL
                else "collection_retryable"
            )
            return OutcomeStatus.ERROR, row.failure_code or default_code
        case _:
            assert_never(row.latest_attempt_status)


def _successful_source_outcome(
    row: SourceRow,
    generated_at: datetime,
) -> tuple[OutcomeStatus, str | None]:
    if row.latest_successful_run_at is None or row.visible_publication_sequence is None:
        return OutcomeStatus.ERROR, "success_publication_missing"
    age_seconds = (generated_at - row.latest_successful_run_at).total_seconds()
    if age_seconds > SOURCE_STALE_SECONDS:
        return OutcomeStatus.PARTIAL, "collection_stale"
    return OutcomeStatus.SUCCESS, None


def _retry_policy(row: SourceRow) -> tuple[bool, str | None]:
    if not row.enabled:
        return False, "source_disabled"
    if row.latest_attempt_status in RETRYABLE_SOURCE_STATUSES:
        return True, None
    return False, RETRY_BLOCK_REASONS[row.latest_attempt_status]


def _engagement_status(comments_count: int | None, score: int | None) -> OutcomeStatus:
    if comments_count is None and score is None:
        return OutcomeStatus.UNKNOWN
    if comments_count is None or score is None:
        return OutcomeStatus.PARTIAL
    return OutcomeStatus.SUCCESS
