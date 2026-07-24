from datetime import UTC, date, datetime

from app.domain.enums import ReportRole
from app.reporting.windows import seoul_report_windows


def test_seoul_report_windows_use_primary_day_and_immediate_predecessor() -> None:
    # Given: a Seoul report day.
    report_date = date(2026, 7, 22)

    # When: the canonical P/Q windows are derived.
    windows = seoul_report_windows(report_date)

    # Then: P is D and Q is D-1 with exact half-open UTC boundaries.
    assert windows.primary.role is ReportRole.PRIMARY
    assert windows.primary.date_seoul == date(2026, 7, 22)
    assert windows.primary.start_utc == datetime(2026, 7, 21, 15, tzinfo=UTC)
    assert windows.primary.end_utc == datetime(2026, 7, 22, 15, tzinfo=UTC)
    assert windows.comparison.role is ReportRole.COMPARISON
    assert windows.comparison.date_seoul == date(2026, 7, 21)
    assert windows.comparison.start_utc == datetime(2026, 7, 20, 15, tzinfo=UTC)
    assert windows.comparison.end_utc == datetime(2026, 7, 21, 15, tzinfo=UTC)
