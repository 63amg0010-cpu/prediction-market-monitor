"""Authenticated author-free post query route."""

from typing import Annotated

from fastapi import APIRouter, Header, Query, Response

from app.core.principals import Scope
from app.services.dashboard.filters import PostFilters
from app.services.dashboard.models import PostPage
from app.services.dashboard.ports import DashboardReader, ScopeAuthorizer
from app.services.dashboard.security import require_scope


def create_posts_router(
    authorizer: ScopeAuthorizer, reader: DashboardReader
) -> APIRouter:
    """Create the BFF-read-protected post listing route."""
    router = APIRouter(prefix="/v1/posts", tags=["posts"])

    @router.get("")
    async def list_posts(
        filters: Annotated[PostFilters, Query()],
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> PostPage:
        """Return typed, paginated posts with original source links."""
        _ = await require_scope(authorizer, authorization, Scope.BFF_READ)
        result = await reader.posts(filters)
        response.headers["Cache-Control"] = "no-store"
        return result

    _ = list_posts
    return router
