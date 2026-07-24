"""Minimal Reddit OAuth response models that discard unapproved fields."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class RedditResponseModel(BaseModel):
    """Provider boundary model that ignores raw fields such as author identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class RedditPostPayload(RedditResponseModel):
    """Only Reddit post fields permitted to reach normalization."""

    id: str = Field(min_length=1, max_length=300)
    subreddit: str = Field(min_length=1)
    title: str
    selftext: str = ""
    permalink: str = Field(min_length=1)
    created_utc: float
    num_comments: int | None = Field(default=None, ge=0)
    score: int | None = None


class RedditChild(RedditResponseModel):
    """Reddit listing child envelope."""

    kind: Literal["t3"]
    data: RedditPostPayload


class RedditListingData(RedditResponseModel):
    """Reddit listing pagination envelope."""

    after: str | None = None
    children: tuple[RedditChild, ...]


class RedditListing(RedditResponseModel):
    """Top-level Reddit listing response."""

    kind: Literal["Listing"]
    data: RedditListingData
