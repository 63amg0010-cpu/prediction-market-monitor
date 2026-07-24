"""Authorized Reddit OAuth Data API adapter with no scraping fallback."""

# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from app.domain.enums import AuthorizationStatus, SourcePlatform

from .http_errors import (
    HttpFailure,
    HttpFailureClassification,
    classify_http_failure,
)
from .models import (
    AdapterPage,
    BlockedKind,
    HttpMethod,
    NormalizedItem,
    NormalizedPost,
    PageTermination,
    PreflightBlocked,
    PreflightContext,
    PreflightReady,
    PreflightResult,
    RejectedOversize,
    SourceBlockedError,
)
from .reddit_contracts import (
    ALLOWED_SUBREDDITS,
    MAX_ACCEPTED_PER_SOURCE_RUN,
    REDDIT_CONCURRENCY,
    REDDIT_FIELDS,
    REDDIT_PURPOSE,
    REDDIT_REQUESTS_PER_MINUTE,
    REDDIT_ROUTE,
    RedditFetchRequest,
    RedditOAuthCredentials,
)
from .reddit_http import create_reddit_http_client, fetch_reddit_listing
from .reddit_normalization import normalize_reddit_post

if TYPE_CHECKING:
    import httpx2

    from .reddit_models import RedditPostPayload

__all__ = [
    "ALLOWED_SUBREDDITS",
    "REDDIT_FIELDS",
    "REDDIT_ROUTE",
    "RedditAdapter",
    "RedditFetchRequest",
    "RedditOAuthCredentials",
    "create_reddit_http_client",
]


class RedditAdapter:
    """Reddit adapter bound to a caller-managed asynchronous HTTP client."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        """Bind the adapter to an explicitly managed HTTP client."""
        self._client: httpx2.AsyncClient = client

    @property
    def source(self) -> SourcePlatform:
        """Return the Reddit provider identity."""
        return SourcePlatform.REDDIT

    @staticmethod
    def preflight(context: PreflightContext) -> PreflightResult:
        """Permit only a current approval matching the complete reviewed scope."""
        decision = context.authorization
        if decision is None:
            return _blocked("current_approved_oauth_authorization_missing")
        match decision.status:
            case AuthorizationStatus.APPROVED:
                pass
            case (
                AuthorizationStatus.DENIED
                | AuthorizationStatus.REVOKED
                | AuthorizationStatus.EXPIRED
            ):
                return _blocked("current_approved_oauth_authorization_missing")
            case unreachable:
                assert_never(unreachable)
        current_window = (
            context.checked_at.tzinfo is not None
            and decision.effective_at.tzinfo is not None
            and decision.expires_at is not None
            and decision.expires_at.tzinfo is not None
            and decision.effective_at <= context.checked_at < decision.expires_at
            and decision.revoked_at is None
        )
        exact_scope = (
            decision.source is SourcePlatform.REDDIT
            and decision.permitted_methods == frozenset({HttpMethod.GET})
            and decision.permitted_routes == frozenset({REDDIT_ROUTE})
            and decision.permitted_fields == REDDIT_FIELDS
            and decision.permitted_subreddits == ALLOWED_SUBREDDITS
            and decision.purpose == REDDIT_PURPOSE
            and decision.requests_per_minute == REDDIT_REQUESTS_PER_MINUTE
            and decision.concurrency == REDDIT_CONCURRENCY
        )
        if not current_window or not exact_scope:
            return _blocked("authorization_scope_mismatch")
        return PreflightReady(decision_id=decision.decision_id)

    async def fetch_page(self, request: RedditFetchRequest) -> AdapterPage:
        """Fetch one OAuth listing page only after the supplied preflight passes."""
        preflight = self.preflight(request.preflight)
        match preflight:
            case PreflightReady():
                pass
            case PreflightBlocked():
                raise SourceBlockedError(self.source, preflight)
            case unreachable:
                assert_never(unreachable)
        remaining = MAX_ACCEPTED_PER_SOURCE_RUN - request.accepted_so_far
        wire_page = await fetch_reddit_listing(
            self._client,
            request,
            min(request.page_size, remaining),
        )
        items = tuple(
            self.normalize(child.data) for child in wire_page.listing.data.children
        )
        accepted_count = 0
        rejected_count = 0
        for item in items:
            match item:
                case NormalizedPost():
                    accepted_count += 1
                case RejectedOversize():
                    rejected_count += 1
                case unreachable:
                    assert_never(unreachable)
        if request.accepted_so_far + accepted_count >= MAX_ACCEPTED_PER_SOURCE_RUN:
            termination = PageTermination.REVIEWED_POST_CAP
        elif (
            wire_page.rate_limit.remaining is not None
            and wire_page.rate_limit.remaining <= 0
        ):
            termination = PageTermination.RATE_LIMIT_PAUSE
        elif wire_page.listing.data.after is None:
            termination = PageTermination.SOURCE_EXHAUSTED
        else:
            termination = PageTermination.CONTINUE
        return AdapterPage(
            items=items,
            next_cursor=wire_page.listing.data.after,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            rate_limit=wire_page.rate_limit,
            termination=termination,
        )

    def normalize(self, raw: RedditPostPayload) -> NormalizedItem:
        """Normalize a parsed Reddit post without author or raw provider fields."""
        return normalize_reddit_post(raw)

    def next_checkpoint(self, page: AdapterPage) -> str | None:
        """Return the opaque Reddit after cursor unchanged."""
        return page.next_cursor

    def classify_error(self, failure: HttpFailure) -> HttpFailureClassification:
        """Classify a redacted Reddit HTTP failure."""
        return classify_http_failure(failure)


def _blocked(code: str) -> PreflightBlocked:
    return PreflightBlocked(kind=BlockedKind.BLOCKED_AUTHORIZATION, code=code)
