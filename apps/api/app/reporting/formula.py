"""Deterministic daily report formula projection."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, override

from app.domain.enums import AnalysisState, ReportRole, ReportStatus, Sentiment
from app.services.configuration.canonical import canonical_bytes

from .coverage import CollectionStatus, ratio_decimal
from .manifest import ManifestBuild, build_manifest
from .rankings import rank_highlights, rank_rising_keywords
from .report_schema import DailyReportPayload

if TYPE_CHECKING:
    from .inputs import ReportRecord
    from .manifest_schema import ReportInputManifest


@dataclass(frozen=True, slots=True)
class ManifestCorruptError(Exception):
    """Typed failure when retained source-coverage scalars disagree with records."""

    reason: str

    @override
    def __str__(self) -> str:
        """Return the stable fail-closed reason code."""
        return self.reason


@dataclass(frozen=True, slots=True)
class ReportBuild:
    """Canonical report bytes paired with their source manifest build."""

    manifest: ManifestBuild
    payload: DailyReportPayload
    canonical_bytes: bytes
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class _EngagementTotals:
    comments_sum: int | None
    comments_known: int
    comments_unknown: int
    score_sum: int | None
    score_known: int
    score_unknown: int


def _relevant(records: tuple[ReportRecord, ...]) -> tuple[ReportRecord, ...]:
    return tuple(
        record
        for record in records
        if record.analysis.state is AnalysisState.VALID
        and record.analysis.relevance is True
    )


def _verify_coverage(payload: ReportInputManifest) -> None:
    actual_keys = {(item.role, item.source_id) for item in payload.records}
    retained_keys = {(item.role, item.source_id) for item in payload.source_coverage}
    if not actual_keys <= retained_keys:
        reason = "source_coverage_missing"
        raise ManifestCorruptError(reason)
    for coverage in payload.source_coverage:
        records = tuple(
            item
            for item in payload.records
            if item.role is coverage.role and item.source_id == coverage.source_id
        )
        valid = sum(item.analysis.state is AnalysisState.VALID for item in records)
        relevant = sum(item.analysis.relevance is True for item in records)
        actual = (len(records), valid, len(records) - valid, relevant)
        retained = (
            coverage.candidate_count,
            coverage.valid_analysis_count,
            coverage.pending_count,
            coverage.relevant_count,
        )
        if actual != retained:
            reason = "source_coverage_count_mismatch"
            raise ManifestCorruptError(reason)
        for record in records:
            dimensions = (record.country, record.platform, record.community)
            coverage_dimensions = (
                coverage.country,
                coverage.platform,
                coverage.community,
            )
            if dimensions != coverage_dimensions:
                reason = "source_coverage_dimension_mismatch"
                raise ManifestCorruptError(reason)


def _engagement(records: tuple[ReportRecord, ...]) -> _EngagementTotals:
    comments = [item.engagement.comments_count for item in records]
    scores = [item.engagement.upvote_or_score for item in records]
    known_comments = [value for value in comments if value is not None]
    known_scores = [value for value in scores if value is not None]
    return _EngagementTotals(
        comments_sum=sum(known_comments) if known_comments else None,
        comments_known=len(known_comments),
        comments_unknown=len(comments) - len(known_comments),
        score_sum=sum(known_scores) if known_scores else None,
        score_known=len(known_scores),
        score_unknown=len(scores) - len(known_scores),
    )


def project_report(payload: ReportInputManifest) -> ReportBuild:
    """Compute and canonicalize daily-report-payload/v1 from retained values."""
    manifest = build_manifest(payload)
    normalized = manifest.payload
    _verify_coverage(normalized)
    primary_records = tuple(
        item for item in normalized.records if item.role is ReportRole.PRIMARY
    )
    comparison_records = tuple(
        item for item in normalized.records if item.role is ReportRole.COMPARISON
    )
    primary = _relevant(primary_records)
    comparison = _relevant(comparison_records)
    candidate = len(primary_records)
    valid = sum(item.analysis.state is AnalysisState.VALID for item in primary_records)
    sentiments = Counter(item.analysis.sentiment for item in primary)
    engagement = _engagement(primary)
    constants = normalized.definitions.constants
    primary_sources_complete = all(
        item.collection_status is CollectionStatus.COMPLETE
        for item in normalized.source_coverage
        if item.role is ReportRole.PRIMARY and item.expected
    )
    analysis_complete = candidate == 0 or (
        valid * constants.complete_coverage_denominator
        >= candidate * constants.complete_coverage_numerator
    )
    status = (
        ReportStatus.COMPLETE
        if primary_sources_complete and analysis_complete
        else ReportStatus.PARTIAL
    )
    report = DailyReportPayload(
        schema="daily-report-payload/v1",
        report_date_seoul=normalized.report_date_seoul,
        windows=normalized.windows,
        source_scope_version=normalized.source_scope_version,
        input_set_hash=manifest.envelope.input_set_hash,
        manifest_payload_sha256=manifest.envelope.manifest_payload_sha256,
        formula_version=normalized.definitions.formula_version,
        formula_hash=normalized.definitions.formula_hash,
        metric_version=normalized.definitions.metric_version,
        metric_hash=normalized.definitions.metric_hash,
        category_version=normalized.definitions.category_version,
        category_hash=normalized.definitions.category_hash,
        candidate_count=candidate,
        valid_analysis_count=valid,
        pending_count=candidate - valid,
        relevant_count=len(primary),
        positive_count=sentiments[Sentiment.POSITIVE],
        neutral_count=sentiments[Sentiment.NEUTRAL],
        negative_count=sentiments[Sentiment.NEGATIVE],
        unknown_sentiment_count=sentiments[None],
        analysis_coverage_numerator=valid,
        analysis_coverage_denominator=candidate,
        analysis_coverage_decimal=ratio_decimal(valid, candidate),
        comments_sum=engagement.comments_sum,
        comments_known_count=engagement.comments_known,
        comments_unknown_count=engagement.comments_unknown,
        score_sum=engagement.score_sum,
        score_known_count=engagement.score_known,
        score_unknown_count=engagement.score_unknown,
        highlights=rank_highlights(primary, comparison, constants),
        rising_keywords=rank_rising_keywords(primary, comparison, constants),
        source_coverage=normalized.source_coverage,
        status=status,
    )
    serialized = canonical_bytes(report)
    return ReportBuild(manifest, report, serialized, sha256(serialized).hexdigest())
