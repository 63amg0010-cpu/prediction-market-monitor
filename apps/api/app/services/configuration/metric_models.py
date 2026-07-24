"""UTC storage, Seoul report-day and nullable metric semantics."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field

from .base_models import ImmutableConfigModel
from .errors import invariant

_invariant = invariant


class ReportWindow(ImmutableConfigModel):
    """Seven-day reporting window definition."""

    boundary: Literal["local_midnight"]
    primary_days: Literal[7]
    comparison_days: Literal[7]
    comparison_relation: Literal["immediately_preceding"]


class ComparisonSemantics(ImmutableConfigModel):
    """Seven-versus-seven comparison semantics."""

    current_days: Literal[7]
    previous_days: Literal[7]
    zero_denominator: Literal["null"]


class DeltaSemantics(ImmutableConfigModel):
    """Null behavior when a comparison denominator is zero."""

    zero_denominator: Literal["null"]


class EngagementSemantics(ImmutableConfigModel):
    """Explicit unknown and nullable engagement behavior."""

    unknown_value: Literal["null"]
    unknown_label: Literal["unknown"]
    sum_when_no_known_values: Literal["null"]
    null_is_not_zero: Literal[True]
    fields: tuple[Literal["comments_count", "upvote_or_score"], ...]


class ReviewedMetrics(ImmutableConfigModel):
    """UTC storage and Seoul report-day metric definitions."""

    schema_name: Literal["monitor.metrics"] = Field(alias="schema")
    version: str
    canonicalization: Literal["json-sort-keys-nfc-v1"]
    review_state: Literal["reviewed_v1"]
    storage_timezone: Literal["UTC"]
    report_timezone: Literal["Asia/Seoul"]
    report_window: ReportWindow = Field(alias="report_day")
    comparison: ComparisonSemantics
    delta: DeltaSemantics
    engagement: EngagementSemantics

    def report_day_for(self, timestamp: datetime) -> date:
        """Convert an aware UTC timestamp to the configured Seoul date."""
        if timestamp.tzinfo is None:
            _invariant(
                "timestamp_timezone_missing",
                "timestamp",
                "UTC-aware timestamps are required",
            )
        report_zone = (
            timezone(timedelta(hours=9))
            if self.report_timezone == "Asia/Seoul"
            else ZoneInfo(self.report_timezone)
        )
        return timestamp.astimezone(report_zone).date()

    def report_day(self, timestamp: datetime) -> date:
        """Convert an aware timestamp to the report day."""
        return self.report_day_for(timestamp)
