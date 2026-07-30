from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import httpx2
import pytest
from app.api.routes.dashboard import create_dashboard_router
from app.api.routes.posts import create_posts_router
from app.core.principals import PrincipalId, Scope
from app.services.dashboard.models import (
    AnalysisSummary,
    AuthorizedService,
    DashboardResponse,
    EngagementSummary,
    MentionSummary,
    OperationsSummary,
    OutcomeStatus,
    PageInfo,
    PostPage,
    ReportItem,
    ReportPage,
)
from fastapi import FastAPI

if TYPE_CHECKING:
    from app.services.dashboard.filters import (
        DashboardFilters,
        PostFilters,
        ReportFilters,
    )
    from pydantic import SecretStr


class _Authorizer:
    async def authorize(
        self, token: SecretStr, required_scope: Scope
    ) -> AuthorizedService:
        del token
        return AuthorizedService(
            principal_id=PrincipalId("bff:search-test"),
            scopes=frozenset({required_scope}),
        )


class _Reader:
    dashboard_filters: DashboardFilters | None = None
    post_filters: PostFilters | None = None

    async def dashboard(self, filters: DashboardFilters) -> DashboardResponse:
        self.dashboard_filters = filters
        return DashboardResponse(
            generated_at=datetime(2026, 7, 28, tzinfo=UTC),
            mentions=MentionSummary(
                current_count=0,
                previous_count=0,
                delta=0,
                delta_rate=None,
                status=OutcomeStatus.SUCCESS,
            ),
            analysis=AnalysisSummary(
                candidate_count=0,
                valid_count=0,
                pending_count=0,
                blocked_count=0,
                coverage=None,
                positive_count=0,
                neutral_count=0,
                negative_count=0,
                unknown_sentiment_count=0,
                status=OutcomeStatus.SUCCESS,
            ),
            engagement=EngagementSummary(
                comments_sum=None,
                comments_known_count=0,
                comments_unknown_count=0,
                score_sum=None,
                score_known_count=0,
                score_unknown_count=0,
                status=OutcomeStatus.UNKNOWN,
            ),
            operations=OperationsSummary(
                last_complete_collection_at=None,
                last_analysis_at=None,
                pending_analysis_count=0,
                blocked_analysis_count=0,
                collection_status=OutcomeStatus.UNKNOWN,
                analysis_status=OutcomeStatus.UNKNOWN,
            ),
            sources=(),
        )

    async def posts(self, filters: PostFilters) -> PostPage:
        self.post_filters = filters
        return PostPage(
            items=(),
            page=PageInfo(
                page=filters.page,
                page_size=filters.page_size,
                total_items=0,
                has_next=False,
            ),
        )

    async def reports(self, filters: ReportFilters) -> ReportPage:
        return ReportPage(
            items=(),
            page=PageInfo(
                page=filters.page,
                page_size=filters.page_size,
                total_items=0,
                has_next=False,
            ),
        )

    async def report(self, report_date: date) -> ReportItem | None:
        del report_date
        return None


def _app(reader: _Reader) -> FastAPI:
    app = FastAPI()
    authorizer = _Authorizer()
    app.include_router(create_dashboard_router(authorizer, reader))
    app.include_router(create_posts_router(authorizer, reader))
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "raw_search", "folded_search"),
    [
        ("/v1/posts", "A😀", "a😀"),
        ("/v1/dashboard", ("A" * 99) + "😀", ("a" * 99) + "😀"),
    ],
)
async def test_search_boundary_accepts_two_and_one_hundred_scalars(
    route: str, raw_search: str, folded_search: str
) -> None:
    # Given: an authenticated API and a boundary-valid raw search.
    reader = _Reader()

    # When: the URL query crosses the FastAPI/Pydantic boundary.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_app(reader)), base_url="https://api.test"
    ) as client:
        response = await client.get(
            route,
            headers={"Authorization": "Bearer token"},
            params={"search": raw_search},
        )

    # Then: it is accepted and stored only after the normative fold.
    assert response.status_code == 200
    filters = reader.post_filters if route == "/v1/posts" else reader.dashboard_filters
    assert filters is not None
    assert filters.search == folded_search


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_search", [" ", "😀", ("a" * 100) + "😀"])
async def test_search_boundary_rejects_blank_one_and_one_hundred_one_scalars(
    raw_search: str,
) -> None:
    # Given: a raw search outside the scalar-count contract.
    reader = _Reader()

    # When: it crosses the posts API boundary.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_app(reader)), base_url="https://api.test"
    ) as client:
        response = await client.get(
            "/v1/posts",
            headers={"Authorization": "Bearer token"},
            params={"search": raw_search},
        )

    # Then: FastAPI rejects it before invoking the reader.
    assert response.status_code == 422
    assert reader.post_filters is None
