"""Reviewed Reddit OAuth request contract."""

from __future__ import annotations

from typing import ClassVar, Final

import httpx2
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .models import (  # noqa: TC001 - Pydantic resolves this at runtime.
    PreflightContext,
)

ALLOWED_SUBREDDITS: Final = frozenset({"Polymarket", "Kalshi", "PredictionMarkets"})
REDDIT_ROUTE: Final = "/r/Polymarket+Kalshi+PredictionMarkets/new"
REDDIT_FIELDS: Final = frozenset(
    {
        "id",
        "subreddit",
        "title",
        "selftext",
        "permalink",
        "created_utc",
        "num_comments",
        "score",
    }
)
REDDIT_API_URL: Final = f"https://oauth.reddit.com{REDDIT_ROUTE}"
REDDIT_PURPOSE: Final = "prediction_market_community_monitoring"
REDDIT_REQUESTS_PER_MINUTE: Final = 30
REDDIT_CONCURRENCY: Final = 1
MAX_PAGE_SIZE: Final = 20
MAX_ACCEPTED_PER_SOURCE_RUN: Final = 20
MAX_CONTENT_BYTES: Final = 262_144
REDDIT_TIMEOUT: Final = httpx2.Timeout(
    connect=5.0,
    read=30.0,
    write=10.0,
    pool=10.0,
)


class RedditOAuthCredentials(BaseModel):
    """Secret OAuth bearer and required identifying user agent."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    access_token: SecretStr = Field(min_length=1)
    user_agent: str = Field(min_length=1, max_length=300, pattern=r"^[^\r\n]+$")


class RedditFetchRequest(BaseModel):
    """One bounded page request with a freshly checked authority snapshot."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    preflight: PreflightContext
    credentials: RedditOAuthCredentials
    cursor: str | None = Field(default=None, max_length=300)
    accepted_so_far: int = Field(ge=0, lt=MAX_ACCEPTED_PER_SOURCE_RUN)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
