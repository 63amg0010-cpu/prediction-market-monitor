from datetime import UTC, date, datetime
from uuid import UUID

from app.domain.enums import (
    AnalysisState,
    Country,
    ReportRole,
    Sentiment,
    SourcePlatform,
)
from app.reporting.coverage import CollectionStatus, SourceCoverage, ratio_decimal
from app.reporting.inputs import (
    AnalysisSnapshot,
    EngagementSelectionState,
    EngagementSnapshot,
    ReportRecord,
    RuleMatchSnapshot,
)
from app.reporting.manifest import (
    AnalysisVersionTuple,
    CategoryMappingSnapshot,
    FormulaConstants,
    GoverningDefinitions,
    ReportInputManifest,
    RuleSetVersion,
)
from app.reporting.windows import seoul_report_windows

REPORT_DATE = date(2026, 7, 22)
NOW = datetime(2026, 7, 22, 1, tzinfo=UTC)
SOURCE_ID = UUID(int=10)


def digest(seed: int) -> str:
    return f"{seed:064x}"


def valid_analysis(
    seed: int,
    *,
    relevance: bool,
    sentiment: Sentiment | None,
) -> AnalysisSnapshot:
    return AnalysisSnapshot(
        state=AnalysisState.VALID,
        analysis_id=UUID(int=1000 + seed),
        output_hash=digest(1000 + seed),
        prompt_version="prompt-v1",
        model_version="model-v1",
        schema_version="analysis-v1",
        analyzed_at=NOW,
        relevance=relevance,
        sentiment=sentiment,
    )


def missing_analysis(state: AnalysisState) -> AnalysisSnapshot:
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


def unavailable_engagement() -> EngagementSnapshot:
    return EngagementSnapshot(
        selection_state=EngagementSelectionState.UNAVAILABLE,
        observation_id=None,
        engagement_hash=None,
        observed_at=None,
        comments_count=None,
        upvote_or_score=None,
    )


def selected_engagement(
    seed: int,
    comments: int | None,
    score: int | None,
) -> EngagementSnapshot:
    return EngagementSnapshot(
        selection_state=EngagementSelectionState.SELECTED,
        observation_id=UUID(int=2000 + seed),
        engagement_hash=digest(2000 + seed),
        observed_at=NOW,
        comments_count=comments,
        upvote_or_score=score,
    )


def rule_match(seed: int, phrase: str, category: str) -> RuleMatchSnapshot:
    return RuleMatchSnapshot(
        match_id=UUID(int=3000 + seed),
        match_hash=digest(3000 + seed),
        rule_id=f"rule-{phrase}",
        rule_set_id="core",
        rule_set_version="1.0.0",
        normalized_phrase=phrase,
        match_present=True,
        mapped_category=category,
    )


def record(
    seed: int,
    role: ReportRole,
    analysis: AnalysisSnapshot,
) -> ReportRecord:
    windows = seoul_report_windows(REPORT_DATE)
    window = windows.primary if role is ReportRole.PRIMARY else windows.comparison
    return ReportRecord(
        ordinal=0,
        role=role,
        source_id=SOURCE_ID,
        country=Country.US,
        platform=SourcePlatform.REDDIT,
        community="r/Polymarket",
        post_version_id=UUID(int=seed),
        post_content_hash=digest(seed),
        published_at_utc=window.start_utc,
        published_date_seoul=window.date_seoul,
        source_publication_sequence=1,
        source_publication_manifest_id=UUID(int=4000),
        source_publication_manifest_hash=digest(4000),
        analysis=analysis,
        rule_matches=(),
        topic_matches=(),
        effective_categories=(),
        engagement=unavailable_engagement(),
    )


def coverage(role: ReportRole, records: tuple[ReportRecord, ...]) -> SourceCoverage:
    selected = tuple(item for item in records if item.role is role)
    valid = sum(item.analysis.state is AnalysisState.VALID for item in selected)
    relevant = sum(item.analysis.relevance is True for item in selected)
    return SourceCoverage(
        role=role,
        source_id=SOURCE_ID,
        country=Country.US,
        platform=SourcePlatform.REDDIT,
        community="r/Polymarket",
        expected=True,
        enabled=True,
        collection_status=CollectionStatus.COMPLETE,
        expected_run_count=1,
        successful_run_count=1,
        failed_run_count=0,
        skipped_run_count=0,
        candidate_count=len(selected),
        valid_analysis_count=valid,
        pending_count=len(selected) - valid,
        relevant_count=relevant,
        cutoff_publication_sequence=1,
        cutoff_publication_manifest_id=UUID(int=4000),
        cutoff_publication_manifest_hash=digest(4000),
        latest_successful_run_started_at=NOW,
        latest_successful_run_finished_at=NOW,
        latest_publication_committed_at=NOW,
        latest_attempt_finished_at=NOW,
        status_observed_at=NOW,
        coverage_numerator=valid,
        coverage_denominator=len(selected),
        coverage_decimal=ratio_decimal(valid, len(selected)),
    )


def manifest_payload(records: tuple[ReportRecord, ...]) -> ReportInputManifest:
    windows = seoul_report_windows(REPORT_DATE)
    definitions = GoverningDefinitions(
        formula_version="formula-v1",
        formula_hash=digest(5000),
        constants=FormulaConstants(
            complete_coverage_numerator=85,
            complete_coverage_denominator=100,
            highlight_limit=5,
            rising_keyword_limit=10,
            rising_keyword_min_primary_count=3,
            zero_denominator="null",
            missing_analysis_semantics="excluded_not_neutral",
        ),
        metric_version="1.0.0",
        metric_hash=digest(5001),
        category_version="1.0.0",
        category_hash=digest(5002),
        rule_sets=(
            RuleSetVersion(
                rule_set_id="core",
                version="1.0.0",
                rules_hash=digest(5003),
            ),
        ),
        analysis_versions=(
            AnalysisVersionTuple(
                prompt_version="prompt-v1",
                model_version="model-v1",
                schema_version="analysis-v1",
            ),
        ),
    )
    base_mapping = CategoryMappingSnapshot(
        input_kind="rule",
        rule_or_topic_key="rule-market",
        version="1.0.0",
        normalized_value="market",
        category="market",
    )
    mappings = {
        (
            base_mapping.input_kind,
            base_mapping.rule_or_topic_key,
            base_mapping.version,
            base_mapping.normalized_value,
        ): base_mapping
    }
    for record_item in records:
        for rule in record_item.rule_matches:
            key = (
                "rule",
                rule.rule_id,
                rule.rule_set_version,
                rule.normalized_phrase,
            )
            _ = mappings.setdefault(
                key,
                CategoryMappingSnapshot(
                    input_kind="rule",
                    rule_or_topic_key=rule.rule_id,
                    version=rule.rule_set_version,
                    normalized_value=rule.normalized_phrase,
                    category=rule.mapped_category,
                ),
            )
        for topic in record_item.topic_matches:
            key = (
                "topic",
                topic.topic_key,
                topic.analysis_schema_version,
                topic.normalized_value,
            )
            _ = mappings.setdefault(
                key,
                CategoryMappingSnapshot(
                    input_kind="topic",
                    rule_or_topic_key=topic.topic_key,
                    version=topic.analysis_schema_version,
                    normalized_value=topic.normalized_value,
                    category=topic.mapped_category,
                ),
            )
    return ReportInputManifest(
        schema="report-input-manifest/v1",
        report_date_seoul=REPORT_DATE,
        windows=(windows.primary, windows.comparison),
        source_scope_version="scope-v1",
        definitions=definitions,
        category_mappings=tuple(mappings.values()),
        records=records,
        source_coverage=(
            coverage(ReportRole.PRIMARY, records),
            coverage(ReportRole.COMPARISON, records),
        ),
    )
