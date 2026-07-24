"""Seoul report-day window construction."""

from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import ClassVar, Final

from pydantic import BaseModel, ConfigDict

from app.domain.enums import ReportRole

from .inputs import UtcTimestamp

SEOUL: Final = timezone(timedelta(hours=9))


class ReportWindow(BaseModel):
    """One half-open Seoul-day window expressed in UTC."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    role: ReportRole
    date_seoul: date
    start_utc: UtcTimestamp
    end_utc: UtcTimestamp


class ReportWindows(BaseModel):
    """Primary day and its immediately preceding comparison day."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    primary: ReportWindow
    comparison: ReportWindow


def seoul_report_windows(report_date: date) -> ReportWindows:
    """Derive exact primary and comparison UTC boundaries for a Seoul date."""
    primary_start = datetime.combine(report_date, time.min, SEOUL)
    comparison_start = primary_start - timedelta(days=1)
    primary = ReportWindow(
        role=ReportRole.PRIMARY,
        date_seoul=report_date,
        start_utc=primary_start.astimezone(UTC),
        end_utc=(primary_start + timedelta(days=1)).astimezone(UTC),
    )
    comparison = ReportWindow(
        role=ReportRole.COMPARISON,
        date_seoul=report_date - timedelta(days=1),
        start_utc=comparison_start.astimezone(UTC),
        end_utc=primary_start.astimezone(UTC),
    )
    return ReportWindows(primary=primary, comparison=comparison)
