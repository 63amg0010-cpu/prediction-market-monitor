"""Authenticated daily report query routes."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Response, status

from app.core.principals import Scope
from app.services.dashboard.filters import ReportFilters
from app.services.dashboard.models import ReportItem, ReportPage
from app.services.dashboard.ports import DashboardReader, ScopeAuthorizer
from app.services.dashboard.security import require_scope


def create_reports_router(
    authorizer: ScopeAuthorizer, reader: DashboardReader
) -> APIRouter:
    """Create BFF-read-protected latest report revision routes."""
    router = APIRouter(prefix="/v1/reports", tags=["reports"])

    @router.get("")
    async def list_reports(
        filters: Annotated[ReportFilters, Query()],
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ReportPage:
        """Return paginated immutable report projections."""
        _ = await require_scope(authorizer, authorization, Scope.BFF_READ)
        result = await reader.reports(filters)
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.get("/{report_date}")
    async def get_report(
        report_date: date,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ReportItem:
        """Return the latest immutable revision for one Seoul date."""
        _ = await require_scope(authorizer, authorization, Scope.BFF_READ)
        result = await reader.report(report_date)
        if result is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        response.headers["Cache-Control"] = "no-store"
        return result

    _ = list_reports, get_report
    return router
