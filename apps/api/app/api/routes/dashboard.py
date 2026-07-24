"""Authenticated dashboard metric and operations route."""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from app.core.principals import Scope
from app.services.dashboard.filters import DashboardFilters
from app.services.dashboard.models import DashboardResponse
from app.services.dashboard.ports import DashboardReader, ScopeAuthorizer
from app.services.dashboard.security import require_scope


def create_dashboard_router(
    authorizer: ScopeAuthorizer, reader: DashboardReader
) -> APIRouter:
    """Create the BFF-read-protected dashboard snapshot route."""
    router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])

    @router.get("")
    async def get_dashboard(
        filters: Annotated[DashboardFilters, Query()],
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> DashboardResponse:
        """Return metrics and evidence without collapsing uncertain states."""
        _ = await require_scope(authorizer, authorization, Scope.BFF_READ)
        result = await reader.dashboard(filters)
        response.headers["Cache-Control"] = "no-store"
        return result

    _ = get_dashboard
    return router
