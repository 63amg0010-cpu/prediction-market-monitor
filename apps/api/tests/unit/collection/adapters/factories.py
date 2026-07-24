from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from app.collection.adapters.models import (
    HttpMethod,
    PreflightContext,
    SourceAuthorizationDecision,
)
from app.collection.adapters.reddit import (
    ALLOWED_SUBREDDITS,
    REDDIT_FIELDS,
    REDDIT_ROUTE,
    RedditOAuthCredentials,
)
from app.domain.enums import AuthorizationStatus, SourcePlatform
from pydantic import SecretStr

NOW = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)
TEST_ACCESS_TOKEN: Final = UUID("22222222-2222-4222-8222-222222222222").hex


def reddit_authorization(
    status: AuthorizationStatus = AuthorizationStatus.APPROVED,
    *,
    routes: frozenset[str] | None = None,
    subreddits: frozenset[str] | None = None,
) -> SourceAuthorizationDecision:
    revoked_at = NOW if status is AuthorizationStatus.REVOKED else None
    return SourceAuthorizationDecision(
        decision_id=UUID("11111111-1111-4111-8111-111111111111"),
        source=SourcePlatform.REDDIT,
        status=status,
        evidence_sha256="a" * 64,
        evidence_location="https://evidence.example.test/reddit-approval",
        issuer="provider",
        reviewer="owner",
        permitted_methods=frozenset({HttpMethod.GET}),
        permitted_routes=(frozenset({REDDIT_ROUTE}) if routes is None else routes),
        permitted_fields=REDDIT_FIELDS,
        permitted_subreddits=(ALLOWED_SUBREDDITS if subreddits is None else subreddits),
        purpose="prediction_market_community_monitoring",
        requests_per_minute=30,
        concurrency=1,
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        revoked_at=revoked_at,
    )


def reddit_context(
    authorization: SourceAuthorizationDecision | None,
) -> PreflightContext:
    return PreflightContext(authorization=authorization, checked_at=NOW)


def reddit_credentials() -> RedditOAuthCredentials:
    return RedditOAuthCredentials(
        access_token=SecretStr(TEST_ACCESS_TOKEN),
        user_agent="prediction-market-monitor/tests-only",
    )
