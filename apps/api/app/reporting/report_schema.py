"""Canonical daily report projection schema."""

from datetime import date
from typing import Literal

from pydantic import Field

from app.domain.enums import ReportStatus

from .coverage import SourceCoverage
from .inputs import ImmutableReportModel, SafeCount, Sha256Hex
from .windows import ReportWindow


class Highlight(ImmutableReportModel):
    """One ranked category comparison and primary net sentiment."""

    category: str
    primary_count: SafeCount
    comparison_count: SafeCount
    delta: int
    delta_rate_numerator: int
    delta_rate_denominator: SafeCount
    delta_rate_decimal: str | None
    net_sentiment: int


class RisingKeyword(ImmutableReportModel):
    """One thresholded and ranked normalized phrase comparison."""

    phrase: str
    primary_count: SafeCount
    comparison_count: SafeCount
    delta: int
    delta_rate_numerator: int
    delta_rate_denominator: SafeCount
    delta_rate_decimal: str | None


class DailyReportPayload(ImmutableReportModel):
    """Every displayed daily-report-payload/v1 scalar and retained source state."""

    schema_name: Literal["daily-report-payload/v1"] = Field(alias="schema")
    report_date_seoul: date
    windows: tuple[ReportWindow, ReportWindow]
    source_scope_version: str
    input_set_hash: Sha256Hex
    manifest_payload_sha256: Sha256Hex
    formula_version: str
    formula_hash: Sha256Hex
    metric_version: str
    metric_hash: Sha256Hex
    category_version: str
    category_hash: Sha256Hex
    candidate_count: SafeCount
    valid_analysis_count: SafeCount
    pending_count: SafeCount
    relevant_count: SafeCount
    positive_count: SafeCount
    neutral_count: SafeCount
    negative_count: SafeCount
    unknown_sentiment_count: SafeCount
    analysis_coverage_numerator: SafeCount
    analysis_coverage_denominator: SafeCount
    analysis_coverage_decimal: str | None
    comments_sum: int | None
    comments_known_count: SafeCount
    comments_unknown_count: SafeCount
    score_sum: int | None
    score_known_count: SafeCount
    score_unknown_count: SafeCount
    highlights: tuple[Highlight, ...]
    rising_keywords: tuple[RisingKeyword, ...]
    source_coverage: tuple[SourceCoverage, ...]
    status: ReportStatus
