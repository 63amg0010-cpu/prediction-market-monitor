"""Pure dashboard metric and status projection semantics."""

from decimal import Decimal

from .models import (
    AnalysisSummary,
    DashboardResponse,
    EngagementSummary,
    MentionSummary,
    OperationsSummary,
    OutcomeStatus,
    SourceStatus,
)
from .sql_rows import MetricRow, OperationRow


def dashboard_projection(
    metrics: MetricRow,
    operation: OperationRow,
    sources: tuple[SourceStatus, ...],
) -> DashboardResponse:
    """Preserve valid zeroes while deriving explicit unknown and blocked states."""
    pending_count = metrics.current_count - metrics.valid_count - metrics.blocked_count
    analysis_status = _analysis_status(metrics, pending_count)
    collection_status = _collection_status(sources)
    delta = metrics.current_count - metrics.previous_count
    delta_rate = (
        None
        if metrics.previous_count == 0
        else Decimal(delta) / Decimal(metrics.previous_count)
    )
    comments_unknown = metrics.current_count - metrics.comments_known_count
    score_unknown = metrics.current_count - metrics.score_known_count
    known_sentiment = (
        metrics.positive_count + metrics.neutral_count + metrics.negative_count
    )
    coverage = (
        None
        if metrics.current_count in {0, metrics.blocked_count}
        else Decimal(metrics.valid_count) / Decimal(metrics.current_count)
    )
    return DashboardResponse(
        generated_at=operation.generated_at,
        mentions=MentionSummary(
            current_count=metrics.current_count,
            previous_count=metrics.previous_count,
            delta=delta,
            delta_rate=delta_rate,
            status=OutcomeStatus.SUCCESS,
        ),
        analysis=AnalysisSummary(
            candidate_count=metrics.current_count,
            valid_count=metrics.valid_count,
            pending_count=pending_count,
            blocked_count=metrics.blocked_count,
            coverage=coverage,
            positive_count=metrics.positive_count,
            neutral_count=metrics.neutral_count,
            negative_count=metrics.negative_count,
            unknown_sentiment_count=metrics.current_count - known_sentiment,
            status=analysis_status,
        ),
        engagement=EngagementSummary(
            comments_sum=metrics.comments_sum,
            comments_known_count=metrics.comments_known_count,
            comments_unknown_count=comments_unknown,
            score_sum=metrics.score_sum,
            score_known_count=metrics.score_known_count,
            score_unknown_count=score_unknown,
            status=_engagement_status(comments_unknown, score_unknown),
        ),
        operations=OperationsSummary(
            last_complete_collection_at=operation.last_complete_collection_at,
            last_analysis_at=operation.last_analysis_at,
            pending_analysis_count=operation.pending_analysis_count,
            blocked_analysis_count=operation.blocked_analysis_count,
            collection_status=collection_status,
            analysis_status=analysis_status,
        ),
        sources=sources,
    )


def _analysis_status(metrics: MetricRow, pending_count: int) -> OutcomeStatus:
    if metrics.current_count == 0:
        return OutcomeStatus.UNKNOWN
    if metrics.blocked_count == metrics.current_count:
        return OutcomeStatus.BLOCKED
    if metrics.valid_count == metrics.current_count:
        return OutcomeStatus.SUCCESS
    if pending_count == metrics.current_count:
        return OutcomeStatus.PENDING
    return OutcomeStatus.PARTIAL


def _engagement_status(comments_unknown: int, score_unknown: int) -> OutcomeStatus:
    if comments_unknown == 0 and score_unknown == 0:
        return OutcomeStatus.SUCCESS
    if comments_unknown > 0 and score_unknown > 0:
        return OutcomeStatus.UNKNOWN
    return OutcomeStatus.PARTIAL


def _collection_status(sources: tuple[SourceStatus, ...]) -> OutcomeStatus:
    if not sources:
        return OutcomeStatus.UNKNOWN
    if all(source.status is OutcomeStatus.SUCCESS for source in sources):
        return OutcomeStatus.SUCCESS
    if all(source.status is OutcomeStatus.BLOCKED for source in sources):
        return OutcomeStatus.BLOCKED
    if any(source.status is OutcomeStatus.ERROR for source in sources):
        return OutcomeStatus.ERROR
    return OutcomeStatus.PARTIAL
