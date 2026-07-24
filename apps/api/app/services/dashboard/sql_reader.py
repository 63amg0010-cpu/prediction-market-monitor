"""SQLAlchemy implementation of the dashboard read port."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Final, TypedDict, final

from .metric_projection import dashboard_projection
from .models import (
    DashboardResponse,
    PageInfo,
    PostPage,
    ReportItem,
    ReportPage,
)
from .report_rows import ReportRow
from .sql_dashboard_statements import (
    DASHBOARD_METRICS,
    OPERATIONS,
    REPEATABLE_READ,
    SOURCE_EVIDENCE,
)
from .sql_read_statements import (
    POST_COUNT,
    POST_PAGE,
    REPORT_BY_DATE,
    REPORT_COUNT,
    REPORT_PAGE,
)
from .sql_rows import CountRow, MetricRow, OperationRow, PostRow, SourceRow

if TYPE_CHECKING:
    from app.db.session import DatabaseSessions

    from .filters import DashboardFilters, PostFilters, ReportFilters

SEOUL: Final = timezone(timedelta(hours=9))


@final
class SqlAlchemyDashboardReader:
    """Query real persisted posts, reports, metrics, and operational evidence."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        """Bind all queries to one explicit fail-closed session owner."""
        self._sessions = sessions

    async def dashboard(self, filters: DashboardFilters) -> DashboardResponse:
        """Read metrics and source evidence from one repeatable-read snapshot."""
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(REPEATABLE_READ)
            operation = OperationRow.model_validate(
                (await session.execute(OPERATIONS)).mappings().one()
            )
            current_start, current_end = _metric_window(filters, operation.generated_at)
            metrics = MetricRow.model_validate(
                (
                    await session.execute(
                        DASHBOARD_METRICS,
                        {
                            **_filter_parameters(filters),
                            "current_start": current_start,
                            "current_end": current_end,
                            "previous_start": current_start - timedelta(days=7),
                        },
                    )
                )
                .mappings()
                .one()
            )
            sources = tuple(
                SourceRow.model_validate(row).projection(operation.generated_at)
                for row in (await session.execute(SOURCE_EVIDENCE)).mappings()
            )
        return dashboard_projection(metrics, operation, sources)

    async def posts(self, filters: PostFilters) -> PostPage:
        """Read one bounded author-free page with original links."""
        parameters = {
            **_filter_parameters(filters),
            "page_size": filters.page_size,
            "page_offset": (filters.page - 1) * filters.page_size,
        }
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(REPEATABLE_READ)
            count = CountRow.model_validate(
                (await session.execute(POST_COUNT, parameters)).mappings().one()
            )
            items = tuple(
                PostRow.model_validate(row).projection()
                for row in (await session.execute(POST_PAGE, parameters)).mappings()
            )
        return PostPage(
            items=items,
            page=_page_info(filters.page, filters.page_size, count.total_items),
        )

    async def reports(self, filters: ReportFilters) -> ReportPage:
        """Read one bounded page of latest immutable report revisions."""
        parameters = {
            "date_from": filters.date_from,
            "date_to": filters.date_to,
            "status": None if filters.status is None else filters.status.value,
            "page_size": filters.page_size,
            "page_offset": (filters.page - 1) * filters.page_size,
        }
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(REPEATABLE_READ)
            count = CountRow.model_validate(
                (await session.execute(REPORT_COUNT, parameters)).mappings().one()
            )
            items = tuple(
                ReportRow.model_validate(row).projection()
                for row in (await session.execute(REPORT_PAGE, parameters)).mappings()
            )
        return ReportPage(
            items=items,
            page=_page_info(filters.page, filters.page_size, count.total_items),
        )

    async def report(self, report_date: date) -> ReportItem | None:
        """Read the latest immutable report revision for one Seoul date."""
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(REPEATABLE_READ)
            row = (
                (await session.execute(REPORT_BY_DATE, {"report_date": report_date}))
                .mappings()
                .one_or_none()
            )
        return None if row is None else ReportRow.model_validate(row).projection()


class _FilterParameters(TypedDict):
    country: str | None
    source_id: str | None
    keyword: str | None
    published_from: datetime | None
    published_to: datetime | None


def _filter_parameters(filters: DashboardFilters) -> _FilterParameters:
    return {
        "country": None if filters.country is None else filters.country.value,
        "source_id": None if filters.source_id is None else str(filters.source_id),
        "keyword": filters.keyword,
        "published_from": filters.published_from,
        "published_to": filters.published_to,
    }


def _metric_window(
    filters: DashboardFilters, generated_at: datetime
) -> tuple[datetime, datetime]:
    if filters.published_from is not None and filters.published_to is not None:
        return filters.published_from, filters.published_to
    local = generated_at.astimezone(SEOUL)
    local_midnight = datetime(local.year, local.month, local.day, tzinfo=SEOUL)
    completed_end = local_midnight.astimezone(UTC)
    return completed_end - timedelta(days=7), completed_end


def _page_info(page: int, page_size: int, total_items: int) -> PageInfo:
    return PageInfo(
        page=page,
        page_size=page_size,
        total_items=total_items,
        has_next=page * page_size < total_items,
    )
