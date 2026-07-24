from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import httpx2
import pytest
from app.api.routes.dashboard import create_dashboard_router
from app.api.routes.posts import create_posts_router
from app.api.routes.reports import create_reports_router
from app.core.errors import install_error_handlers
from app.core.principals import PrincipalId, Scope
from app.domain.enums import AnalysisState, Country, ReportStatus
from app.services.dashboard.filters import DashboardFilters, PostFilters, ReportFilters
from app.services.dashboard.models import (
    AnalysisSummary,
    AuthorizedService,
    DashboardResponse,
    EngagementSummary,
    MentionSummary,
    OperationsSummary,
    OutcomeStatus,
    PageInfo,
    PostItem,
    PostPage,
    ReportItem,
    ReportPage,
    ReproductionStatus,
)
from fastapi import FastAPI
from pydantic import AnyHttpUrl

if TYPE_CHECKING:
    from pydantic import SecretStr

NOW = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)
SOURCE_ID = UUID("4368f7f2-21bd-4b3a-b26e-15036523cb6d")
POST_ID = UUID("209cbeff-1ac8-4ef7-9da0-13aad9ddd086")
REPORT_ID = UUID("14ecdf7f-9917-4bea-a660-dd33930ddc9f")


class _Authorizer:
    calls: list[tuple[str, Scope]]

    def __init__(self) -> None:
        self.calls = []

    async def authorize(
        self, token: SecretStr, required_scope: Scope
    ) -> AuthorizedService:
        self.calls.append((token.get_secret_value(), required_scope))
        return AuthorizedService(
            principal_id=PrincipalId("bff:dashboard"),
            scopes=frozenset({required_scope}),
        )


class _Reader:
    dashboard_filters: DashboardFilters | None = None
    post_filters: PostFilters | None = None
    report_filters: ReportFilters | None = None

    async def dashboard(self, filters: DashboardFilters) -> DashboardResponse:
        self.dashboard_filters = filters
        return DashboardResponse(
            generated_at=NOW,
            mentions=MentionSummary(
                current_count=0,
                previous_count=0,
                delta=0,
                delta_rate=None,
                status=OutcomeStatus.SUCCESS,
            ),
            analysis=AnalysisSummary(
                candidate_count=3,
                valid_count=0,
                pending_count=0,
                blocked_count=3,
                coverage=None,
                positive_count=0,
                neutral_count=0,
                negative_count=0,
                unknown_sentiment_count=3,
                status=OutcomeStatus.BLOCKED,
            ),
            engagement=EngagementSummary(
                comments_sum=None,
                comments_known_count=0,
                comments_unknown_count=3,
                score_sum=None,
                score_known_count=0,
                score_unknown_count=3,
                status=OutcomeStatus.UNKNOWN,
            ),
            operations=OperationsSummary(
                last_complete_collection_at=None,
                last_analysis_at=None,
                pending_analysis_count=0,
                blocked_analysis_count=3,
                collection_status=OutcomeStatus.UNKNOWN,
                analysis_status=OutcomeStatus.BLOCKED,
            ),
            sources=(),
        )

    async def posts(self, filters: PostFilters) -> PostPage:
        self.post_filters = filters
        return PostPage(
            items=(
                PostItem(
                    id=POST_ID,
                    source_id=SOURCE_ID,
                    source_name="Reddit /r/Kalshi",
                    country=Country.US,
                    title="Will this market resolve?",
                    original_url=AnyHttpUrl(
                        "https://www.reddit.com/r/Kalshi/comments/example"
                    ),
                    published_at=NOW,
                    analysis_state=AnalysisState.PENDING,
                    relevance=None,
                    sentiment=None,
                    comments_count=None,
                    score=None,
                    engagement_status=OutcomeStatus.UNKNOWN,
                ),
            ),
            page=PageInfo(page=2, page_size=25, total_items=26, has_next=False),
        )

    async def reports(self, filters: ReportFilters) -> ReportPage:
        self.report_filters = filters
        return ReportPage(
            items=(
                ReportItem(
                    id=REPORT_ID,
                    report_date_seoul=date(2026, 7, 21),
                    revision=2,
                    status=ReportStatus.PARTIAL,
                    candidate_count=4,
                    relevant_count=1,
                    pending_count=3,
                    analysis_coverage=Decimal("0.25"),
                    comments_sum=None,
                    score_sum=0,
                    highlights=(),
                    rising_keywords=(),
                    source_coverage=(),
                    manifest_id=UUID(int=200),
                    input_set_hash="a" * 64,
                    manifest_payload_sha256="b" * 64,
                    report_payload_sha256="c" * 64,
                    reproduction_status=ReproductionStatus.VERIFIED,
                    created_at=NOW,
                ),
            ),
            page=PageInfo(page=1, page_size=10, total_items=1, has_next=False),
        )

    async def report(self, report_date: date) -> ReportItem | None:
        del report_date
        return (await self.reports(ReportFilters())).items[0]


def _app(authorizer: _Authorizer, reader: _Reader) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_dashboard_router(authorizer, reader))
    app.include_router(create_posts_router(authorizer, reader))
    app.include_router(create_reports_router(authorizer, reader))
    return app


@pytest.mark.asyncio
async def test_dashboard_preserves_zero_unknown_and_blocked_states() -> None:
    # Given
    authorizer = _Authorizer()
    reader = _Reader()

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_app(authorizer, reader)),
        base_url="https://api.test",
    ) as client:
        response = await client.get(
            "/v1/dashboard", headers={"Authorization": "Bearer read-token"}
        )

    # Then
    assert response.status_code == 200
    assert authorizer.calls == [("read-token", Scope.BFF_READ)]
    body = DashboardResponse.model_validate_json(response.content)
    assert body.mentions.current_count == 0
    assert body.mentions.delta_rate is None
    assert body.analysis.status is OutcomeStatus.BLOCKED
    assert body.engagement.comments_sum is None
    assert body.engagement.status is OutcomeStatus.UNKNOWN


@pytest.mark.asyncio
async def test_posts_pass_typed_filters_and_return_original_link() -> None:
    # Given
    authorizer = _Authorizer()
    reader = _Reader()

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_app(authorizer, reader)),
        base_url="https://api.test",
    ) as client:
        response = await client.get(
            "/v1/posts",
            headers={"Authorization": "Bearer read-token"},
            params={
                "country": "us",
                "source_id": str(SOURCE_ID),
                "keyword": "Kalshi",
                "published_from": "2026-07-15T00:00:00Z",
                "published_to": "2026-07-22T00:00:00Z",
                "page": 2,
                "page_size": 25,
            },
        )

    # Then
    assert response.status_code == 200
    assert reader.post_filters == PostFilters(
        country=Country.US,
        source_id=SOURCE_ID,
        keyword="Kalshi",
        published_from=datetime(2026, 7, 15, tzinfo=UTC),
        published_to=datetime(2026, 7, 22, tzinfo=UTC),
        page=2,
        page_size=25,
    )
    body = PostPage.model_validate_json(response.content)
    assert str(body.items[0].original_url).startswith("https://www.reddit.com/")
    assert body.items[0].comments_count is None


@pytest.mark.asyncio
async def test_reports_expose_revision_and_nullable_engagement() -> None:
    # Given
    authorizer = _Authorizer()
    reader = _Reader()

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_app(authorizer, reader)),
        base_url="https://api.test",
    ) as client:
        response = await client.get(
            "/v1/reports",
            headers={"Authorization": "Bearer read-token"},
            params={"status": "partial", "page": 1, "page_size": 10},
        )

    # Then
    assert response.status_code == 200
    assert reader.report_filters == ReportFilters(
        status=ReportStatus.PARTIAL, page=1, page_size=10
    )
    item = ReportPage.model_validate_json(response.content).items[0]
    assert item.revision == 2
    assert item.comments_sum is None
    assert item.score_sum == 0
    assert item.manifest_id == UUID(int=200)
    assert item.reproduction_status is ReproductionStatus.VERIFIED


@pytest.mark.asyncio
async def test_read_routes_fail_closed_without_bearer_token() -> None:
    # Given
    authorizer = _Authorizer()
    reader = _Reader()

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_app(authorizer, reader)),
        base_url="https://api.test",
    ) as client:
        response = await client.get("/v1/dashboard")

    # Then
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"
    assert authorizer.calls == []
