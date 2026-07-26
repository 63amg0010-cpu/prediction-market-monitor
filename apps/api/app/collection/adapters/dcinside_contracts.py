"""Reviewed DCInside prediction-market gallery request contract."""

from __future__ import annotations

from typing import ClassVar, Final

import httpx2
from pydantic import BaseModel, ConfigDict, Field

from .models import PreflightContext  # noqa: TC001

DCINSIDE_GALLERY_ID: Final = "predictionmarket"
DCINSIDE_LIST_ROUTE: Final = (
    f"/mini/board/lists/?id={DCINSIDE_GALLERY_ID}"
)
DCINSIDE_VIEW_ROUTE: Final = (
    f"/mini/board/view/?id={DCINSIDE_GALLERY_ID}&no={{post_id}}"
)
DCINSIDE_ROUTES: Final = frozenset(
    {DCINSIDE_LIST_ROUTE, DCINSIDE_VIEW_ROUTE}
)
DCINSIDE_FIELDS: Final = frozenset(
    {
        "source_post_id",
        "canonical_url",
        "title",
        "body",
        "published_at",
        "comments_count",
        "upvote_or_score",
    }
)
DCINSIDE_ORIGIN: Final = "https://gall.dcinside.com"
DCINSIDE_LIST_URL: Final = f"{DCINSIDE_ORIGIN}{DCINSIDE_LIST_ROUTE}"
DCINSIDE_PURPOSE: Final = (
    "personal_noncommercial_prediction_market_monitoring_no_model_training"
)
DCINSIDE_REQUESTS_PER_MINUTE: Final = 30
DCINSIDE_CONCURRENCY: Final = 1
MAX_PAGE_SIZE: Final = 20
MAX_ACCEPTED_PER_SOURCE_RUN: Final = 20
MAX_CONTENT_BYTES: Final = 262_144
DCINSIDE_TIMEOUT: Final = httpx2.Timeout(
    connect=5.0,
    read=30.0,
    write=10.0,
    pool=10.0,
)


class DCInsideFetchRequest(BaseModel):
    """One bounded fetch of the reviewed public gallery page."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    preflight: PreflightContext
    cursor: str | None = Field(default=None, max_length=300)
    accepted_so_far: int = Field(ge=0, lt=MAX_ACCEPTED_PER_SOURCE_RUN)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    user_agent: str = Field(min_length=1, max_length=300, pattern=r"^[^\r\n]+$")
