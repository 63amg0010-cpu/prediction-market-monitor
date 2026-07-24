"""Authorization and query ports consumed by HTTP routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import date

    from pydantic import SecretStr

    from app.core.principals import Scope

    from .filters import DashboardFilters, PostFilters, ReportFilters
    from .models import (
        AuthorizedService,
        DashboardResponse,
        PostPage,
        ReportItem,
        ReportPage,
    )


class ScopeAuthorizer(Protocol):
    """Validate a service bearer token for one exact required scope."""

    async def authorize(
        self, token: SecretStr, required_scope: Scope
    ) -> AuthorizedService:
        """Return a durable-state-checked service principal."""
        ...


class DashboardReader(Protocol):
    """Read consistent, repository-backed dashboard projections."""

    async def dashboard(self, filters: DashboardFilters) -> DashboardResponse:
        """Return the metric and status snapshot for typed filters."""
        ...

    async def posts(self, filters: PostFilters) -> PostPage:
        """Return current post projections with original source URLs."""
        ...

    async def reports(self, filters: ReportFilters) -> ReportPage:
        """Return paginated latest report revisions."""
        ...

    async def report(self, report_date: date) -> ReportItem | None:
        """Return the latest revision for one Seoul report date."""
        ...
