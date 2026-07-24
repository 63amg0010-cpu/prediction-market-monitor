"""Projection of repeatable-read SQL rows into retained report records."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from typing import TYPE_CHECKING, assert_never

from app.domain.enums import AnalysisState, QueueStatus

from .input_policy import ReportAssemblyError, ReportAssemblyPolicy
from .inputs import (
    AnalysisSnapshot,
    EngagementSelectionState,
    EngagementSnapshot,
    ReportRecord,
    RuleMatchSnapshot,
    TopicMatchSnapshot,
)
from .sql_input_rows import (
    MatchSqlRow,
    RecordSqlRow,
    ReportInputQueryRows,
    publication_hash,
)
from .windows import ReportWindow, seoul_report_windows

if TYPE_CHECKING:
    from uuid import UUID


def report_records(
    policy: ReportAssemblyPolicy,
    rows: ReportInputQueryRows,
) -> tuple[ReportRecord, ...]:
    """Materialize every body-free formula input selected for P and Q."""
    windows = seoul_report_windows(rows.report_date_seoul)
    matches_by_version: defaultdict[UUID, list[MatchSqlRow]] = defaultdict(list)
    for match in rows.matches:
        matches_by_version[match.post_version_id].append(match)
    records: list[ReportRecord] = []
    for row in rows.records:
        window = _window(row, windows.primary, windows.comparison)
        analysis = _analysis(row)
        rule_matches = tuple(
            _rule_match(match, policy)
            for match in matches_by_version[row.post_version_id]
        )
        topic_matches = _topic_matches(row, analysis, policy)
        publication = row.publication()
        records.append(
            ReportRecord(
                ordinal=0,
                role=window.role,
                source_id=row.source_id,
                country=row.country,
                platform=row.platform,
                community=row.community,
                post_version_id=row.post_version_id,
                post_content_hash=row.post_content_hash,
                published_at_utc=row.published_at_utc,
                published_date_seoul=window.date_seoul,
                source_publication_sequence=publication.sequence,
                source_publication_manifest_id=publication.id,
                source_publication_manifest_hash=publication_hash(publication),
                analysis=analysis,
                rule_matches=rule_matches,
                topic_matches=topic_matches,
                effective_categories=(),
                engagement=_engagement(row),
            )
        )
    return tuple(records)


def _window(
    row: RecordSqlRow,
    primary: ReportWindow,
    comparison: ReportWindow,
) -> ReportWindow:
    for window in (primary, comparison):
        if window.start_utc <= row.published_at_utc < window.end_utc:
            return window
    reason = "report_record_outside_windows"
    raise ReportAssemblyError(reason)


def _analysis(row: RecordSqlRow) -> AnalysisSnapshot:
    if row.analysis_id is not None:
        if row.analysis_state is None:
            reason = "report_analysis_state_missing"
            raise ReportAssemblyError(reason)
        if row.analysis_state is AnalysisState.VALID:
            return AnalysisSnapshot(
                state=row.analysis_state,
                analysis_id=row.analysis_id,
                output_hash=row.output_hash,
                prompt_version=row.prompt_version,
                model_version=row.model_version,
                schema_version=row.schema_version,
                analyzed_at=row.analyzed_at,
                relevance=row.relevance,
                sentiment=row.sentiment,
            )
        return _empty_analysis(row.analysis_state)
    if row.analysis_state is not None:
        reason = "report_analysis_identity_missing"
        raise ReportAssemblyError(reason)
    match row.queue_status:
        case None | QueueStatus.PENDING | QueueStatus.LEASED:
            state = AnalysisState.PENDING
        case QueueStatus.SUCCEEDED:
            state = AnalysisState.INVALID_OUTPUT
        case QueueStatus.BLOCKED_CAPABILITY:
            state = AnalysisState.BLOCKED_CAPABILITY
        case QueueStatus.FAILED_RETRYABLE:
            state = AnalysisState.FAILED_RETRYABLE
        case QueueStatus.FAILED_TERMINAL:
            state = AnalysisState.FAILED_TERMINAL
        case _:
            assert_never(row.queue_status)
    return _empty_analysis(state)


def _empty_analysis(state: AnalysisState) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        state=state,
        analysis_id=None,
        output_hash=None,
        prompt_version=None,
        model_version=None,
        schema_version=None,
        analyzed_at=None,
        relevance=None,
        sentiment=None,
    )


def _rule_match(
    row: MatchSqlRow,
    policy: ReportAssemblyPolicy,
) -> RuleMatchSnapshot:
    mapping = policy.rule_mapping(row.normalized_phrase, row.rule_set_version)
    rule_sets = tuple(
        item
        for item in policy.definitions.rule_sets
        if item.version == row.rule_set_version and item.rules_hash == row.rule_set_hash
    )
    categories = {row.stored_category, row.rule_category, mapping.category}
    if len(rule_sets) != 1 or len(categories) != 1:
        reason = "report_rule_provenance_mismatch"
        raise ReportAssemblyError(reason)
    return RuleMatchSnapshot(
        match_id=row.match_id,
        match_hash=row.match_hash,
        rule_id=mapping.rule_or_topic_key,
        rule_set_id=rule_sets[0].rule_set_id,
        rule_set_version=row.rule_set_version,
        normalized_phrase=row.normalized_phrase,
        match_present=row.match_present,
        mapped_category=mapping.category,
    )


def _topic_matches(
    row: RecordSqlRow,
    analysis: AnalysisSnapshot,
    policy: ReportAssemblyPolicy,
) -> tuple[TopicMatchSnapshot, ...]:
    if analysis.state is not AnalysisState.VALID:
        return ()
    if analysis.schema_version is None or row.topics is None:
        reason = "report_analysis_topics_missing"
        raise ReportAssemblyError(reason)
    normalized = {
        unicodedata.normalize("NFC", value.strip().casefold()) for value in row.topics
    }
    if "" in normalized:
        reason = "report_analysis_topic_empty"
        raise ReportAssemblyError(reason)
    return tuple(
        TopicMatchSnapshot(
            topic_key=mapping.rule_or_topic_key,
            normalized_value=value,
            analysis_schema_version=analysis.schema_version,
            mapped_category=mapping.category,
        )
        for value in sorted(normalized)
        for mapping in (policy.topic_mapping(value, analysis.schema_version),)
    )


def _engagement(row: RecordSqlRow) -> EngagementSnapshot:
    if row.engagement_observation_id is None:
        evidence = (
            row.engagement_hash,
            row.engagement_observed_at,
            row.comments_count,
            row.upvote_or_score,
        )
        if any(value is not None for value in evidence):
            reason = "report_engagement_identity_missing"
            raise ReportAssemblyError(reason)
        return EngagementSnapshot(
            selection_state=EngagementSelectionState.UNAVAILABLE,
            observation_id=None,
            engagement_hash=None,
            observed_at=None,
            comments_count=None,
            upvote_or_score=None,
        )
    return EngagementSnapshot(
        selection_state=EngagementSelectionState.SELECTED,
        observation_id=row.engagement_observation_id,
        engagement_hash=row.engagement_hash,
        observed_at=row.engagement_observed_at,
        comments_count=row.comments_count,
        upvote_or_score=row.upvote_or_score,
    )
