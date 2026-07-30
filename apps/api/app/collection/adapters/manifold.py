"""Bounded author-free Manifold source adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Final

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AuthorizationStatus, SourcePlatform

from .http_errors import (
    HttpFailure,
    HttpFailureClassification,
    classify_http_failure,
)
from .manifold_http import (
    MANIFOLD_COMMENTS_ROUTE,
    MANIFOLD_MARKETS_ROUTE,
    create_manifold_http_client,
    fetch_manifold_comments,
    fetch_manifold_markets,
)
from .manifold_page import ManifoldPageAccumulator, empty_manifold_page
from .models import (
    AdapterPage,
    BlockedKind,
    HttpMethod,
    PageTermination,
    PreflightBlocked,
    PreflightContext,
    PreflightReady,
    PreflightResult,
    SourceBlockedError,
)

if TYPE_CHECKING:
    import httpx2

MANIFOLD_ROUTES: Final = frozenset(
    {MANIFOLD_MARKETS_ROUTE, MANIFOLD_COMMENTS_ROUTE}
)
MANIFOLD_FIELDS: Final = frozenset(
    {
        "market.id",
        "market.question",
        "market.market_slug",
        "market.neutral_url",
        "comment.id",
        "comment.contractId",
        "comment.createdTime",
        "comment.content.text",
    }
)
MANIFOLD_PURPOSE: Final = (
    "personal_noncommercial_prediction_market_monitoring_no_model_training"
)
MANIFOLD_REQUESTS_PER_MINUTE: Final = 30
MANIFOLD_CONCURRENCY: Final = 1
MAX_MANIFOLD_ACCEPTED_PER_RUN: Final = 20


class ManifoldFetchRequest(BaseModel):
    """One logical bounded Manifold collection page."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    preflight: PreflightContext
    page_ordinal: int = Field(ge=0)
    accepted_so_far: int = Field(ge=0, le=MAX_MANIFOLD_ACCEPTED_PER_RUN)


class ManifoldAdapter:
    """Collect reviewed public comments without provider identity fields."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        """Bind the adapter to a caller-managed single-connection client."""
        self._client: httpx2.AsyncClient = client

    @property
    def source(self) -> SourcePlatform:
        """Return the Manifold provider identity."""
        return SourcePlatform.MANIFOLD

    @staticmethod
    def preflight(context: PreflightContext) -> PreflightResult:
        """Permit only the current exact owner-approved collection envelope."""
        decision = context.authorization
        if decision is None:
            return _blocked("current_approved_authorization_missing")
        match decision.status:  # noqa: RUF100  # noqa: MATCH_OK
            case AuthorizationStatus.APPROVED:
                pass
            case (
                AuthorizationStatus.DENIED
                | AuthorizationStatus.REVOKED
                | AuthorizationStatus.EXPIRED
            ):
                return _blocked("current_approved_authorization_missing")
        current_window = (
            context.checked_at.tzinfo is not None
            and decision.effective_at.tzinfo is not None
            and decision.expires_at is not None
            and decision.expires_at.tzinfo is not None
            and decision.effective_at <= context.checked_at < decision.expires_at
            and decision.revoked_at is None
        )
        exact_scope = (
            decision.source is SourcePlatform.MANIFOLD
            and decision.permitted_methods == frozenset({HttpMethod.GET})
            and decision.permitted_routes == MANIFOLD_ROUTES
            and decision.permitted_fields == MANIFOLD_FIELDS
            and decision.permitted_subreddits == frozenset()
            and decision.purpose == MANIFOLD_PURPOSE
            and decision.requests_per_minute == MANIFOLD_REQUESTS_PER_MINUTE
            and decision.concurrency == MANIFOLD_CONCURRENCY
        )
        if not current_window or not exact_scope:
            return _blocked("authorization_scope_mismatch")
        return PreflightReady(decision_id=decision.decision_id)

    async def fetch_page(self, request: ManifoldFetchRequest) -> AdapterPage:
        """Fetch one bounded logical page only after the exact preflight."""
        preflight = self.preflight(request.preflight)
        match preflight:  # noqa: RUF100  # noqa: MATCH_OK
            case PreflightReady():
                pass
            case PreflightBlocked():
                raise SourceBlockedError(self.source, preflight)
        if request.page_ordinal > 0:
            return empty_manifold_page(PageTermination.SOURCE_EXHAUSTED)
        if request.accepted_so_far >= MAX_MANIFOLD_ACCEPTED_PER_RUN:
            return empty_manifold_page(PageTermination.REVIEWED_POST_CAP)
        markets = await fetch_manifold_markets(self._client)
        accepted_limit = (
            MAX_MANIFOLD_ACCEPTED_PER_RUN - request.accepted_so_far
        )
        accumulator = ManifoldPageAccumulator(accepted_limit)
        for market in markets:
            comments = await fetch_manifold_comments(self._client, market.id)
            for comment in comments:
                termination = accumulator.add(market, comment)
                if termination is not None:
                    return accumulator.page(termination)
        return accumulator.page(PageTermination.SOURCE_EXHAUSTED)

    @staticmethod
    def next_checkpoint(page: AdapterPage) -> str | None:
        """Return no cursor because one invocation is one logical page."""
        del page

    @staticmethod
    def classify_error(failure: HttpFailure) -> HttpFailureClassification:
        """Classify a redacted Manifold HTTP failure."""
        return classify_http_failure(failure)


def _blocked(code: str) -> PreflightBlocked:
    return PreflightBlocked(
        kind=BlockedKind.BLOCKED_AUTHORIZATION,
        code=code,
    )

__all__ = (
    "MANIFOLD_FIELDS",
    "MANIFOLD_PURPOSE",
    "MANIFOLD_ROUTES",
    "ManifoldAdapter",
    "ManifoldFetchRequest",
    "create_manifold_http_client",
)
