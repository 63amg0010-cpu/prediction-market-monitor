"""Reviewed DCInside public-gallery adapter."""

# pyright: reportUnnecessaryComparison=false

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from app.collection.normalizer import compute_body_bytes, compute_content_hash
from app.domain.enums import AuthorizationStatus, SourcePlatform

from .dcinside_contracts import (
    DCINSIDE_CONCURRENCY,
    DCINSIDE_FIELDS,
    DCINSIDE_PURPOSE,
    DCINSIDE_REQUESTS_PER_MINUTE,
    DCINSIDE_ROUTES,
    MAX_ACCEPTED_PER_SOURCE_RUN,
    MAX_CONTENT_BYTES,
    DCInsideFetchRequest,
)
from .dcinside_http import create_dcinside_http_client, fetch_dcinside_documents
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
    RateLimitSnapshot,
    RejectedOversize,
    SourceBlockedError,
)

if TYPE_CHECKING:
    import httpx2

    from .dcinside_html import DCInsidePostDocument

__all__ = (
    "DCINSIDE_FIELDS",
    "DCINSIDE_PURPOSE",
    "DCINSIDE_ROUTES",
    "DCInsideAdapter",
    "DCInsideFetchRequest",
    "create_dcinside_http_client",
)


class DCInsideAdapter:
    """Collect the reviewed prediction-market mini gallery without author data."""

    def __init__(self, client: httpx2.AsyncClient) -> None:
        """Bind the adapter to a caller-managed HTTP client."""
        self._client: httpx2.AsyncClient = client

    @property
    def source(self) -> SourcePlatform:
        """Return the DCInside provider identity."""
        return SourcePlatform.DCINSIDE

    @staticmethod
    def preflight(context: PreflightContext) -> PreflightResult:
        """Permit only a current approval matching the exact reviewed scope."""
        decision = context.authorization
        if decision is None:
            return _blocked("current_approved_authorization_missing")
        match decision.status:
            case AuthorizationStatus.APPROVED:
                pass
            case (
                AuthorizationStatus.DENIED
                | AuthorizationStatus.REVOKED
                | AuthorizationStatus.EXPIRED
            ):
                return _blocked("current_approved_authorization_missing")
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
            decision.source is SourcePlatform.DCINSIDE
            and decision.permitted_methods == frozenset({HttpMethod.GET})
            and decision.permitted_routes == DCINSIDE_ROUTES
            and decision.permitted_fields == DCINSIDE_FIELDS
            and decision.permitted_subreddits == frozenset()
            and decision.purpose == DCINSIDE_PURPOSE
            and decision.requests_per_minute == DCINSIDE_REQUESTS_PER_MINUTE
            and decision.concurrency == DCINSIDE_CONCURRENCY
        )
        if not current_window or not exact_scope:
            return _blocked("authorization_scope_mismatch")
        return PreflightReady(decision_id=decision.decision_id)

    async def fetch_page(self, request: DCInsideFetchRequest) -> AdapterPage:
        """Fetch the bounded current gallery page after authorization preflight."""
        preflight = self.preflight(request.preflight)
        match preflight:
            case PreflightReady():
                pass
            case PreflightBlocked():
                raise SourceBlockedError(self.source, preflight)
            case unreachable:
                assert_never(unreachable)
        if request.cursor is not None:
            return _empty_page()
        remaining = MAX_ACCEPTED_PER_SOURCE_RUN - request.accepted_so_far
        documents = await fetch_dcinside_documents(
            self._client,
            request,
            min(request.page_size, remaining),
        )
        items = tuple(_normalize(document) for document in documents)
        accepted_count = sum(
            1 for item in items if isinstance(item, NormalizedPost)
        )
        rejected_count = len(items) - accepted_count
        return AdapterPage(
            items=items,
            next_cursor=None,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            rate_limit=_empty_rate_limit(),
            termination=PageTermination.SOURCE_EXHAUSTED,
        )


def _normalize(document: DCInsidePostDocument) -> NormalizedItem:
    canonical_url = (
        "https://gall.dcinside.com/mini/board/view/"
        f"?id=predictionmarket&no={document.source_post_id}"
    )
    content_hash = compute_content_hash(document.title, document.body)
    size_bytes = compute_body_bytes(document.body)
    if size_bytes > MAX_CONTENT_BYTES:
        return RejectedOversize(
            source=SourcePlatform.DCINSIDE,
            source_post_id=document.source_post_id,
            canonical_url=canonical_url,
            content_hash=content_hash,
            size_bytes=size_bytes,
        )
    return NormalizedPost(
        source=SourcePlatform.DCINSIDE,
        source_post_id=document.source_post_id,
        canonical_url=canonical_url,
        title=document.title,
        body=document.body,
        published_at=document.published_at,
        language="ko",
        comments_count=document.comments_count,
        upvote_or_score=document.upvote_or_score,
        content_hash=content_hash,
        size_bytes=size_bytes,
    )


def _blocked(code: str) -> PreflightBlocked:
    return PreflightBlocked(
        kind=BlockedKind.BLOCKED_AUTHORIZATION,
        code=code,
    )


def _empty_rate_limit() -> RateLimitSnapshot:
    return RateLimitSnapshot(
        used=None,
        remaining=None,
        reset_after_seconds=None,
        retry_after_seconds=None,
    )


def _empty_page() -> AdapterPage:
    return AdapterPage(
        items=(),
        next_cursor=None,
        accepted_count=0,
        rejected_count=0,
        rate_limit=_empty_rate_limit(),
        termination=PageTermination.SOURCE_EXHAUSTED,
    )
