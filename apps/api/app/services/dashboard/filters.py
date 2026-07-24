"""Validated dashboard filter and pagination boundaries."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - Pydantic resolves at runtime.
from typing import Annotated, ClassVar, Final, Self
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from app.domain.enums import (  # noqa: TC001 - Pydantic resolves these at runtime.
    Country,
    ReportStatus,
)

Keyword = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]
INCOMPLETE_PUBLISHED_WINDOW: Final = "incomplete_published_window"
INVALID_PUBLISHED_WINDOW: Final = "invalid_published_window"
INCOMPLETE_REPORT_RANGE: Final = "incomplete_report_range"
INVALID_REPORT_RANGE: Final = "invalid_report_range"


class DashboardFilters(BaseModel):
    """Country, source, keyword, and aware UTC window filters."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    country: Country | None = None
    source_id: UUID | None = None
    keyword: Keyword | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None

    @model_validator(mode="after")
    def require_complete_aware_window(self) -> Self:
        """Reject partial, naive, or reversed time windows at the HTTP boundary."""
        has_start = self.published_from is not None
        has_end = self.published_to is not None
        if has_start != has_end:
            raise PydanticCustomError(
                INCOMPLETE_PUBLISHED_WINDOW,
                "published window requires both boundaries",
            )
        if self.published_from is None or self.published_to is None:
            return self
        if (
            self.published_from.utcoffset() is None
            or self.published_to.utcoffset() is None
            or self.published_from >= self.published_to
        ):
            raise PydanticCustomError(
                INVALID_PUBLISHED_WINDOW,
                "published window must be aware and increasing",
            )
        return self


class PostFilters(DashboardFilters):
    """Dashboard filters plus bounded post pagination."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class ReportFilters(BaseModel):
    """Date, completeness, and bounded report pagination filters."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    date_from: date | None = None
    date_to: date | None = None
    status: ReportStatus | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=30, ge=1, le=100)

    @model_validator(mode="after")
    def require_increasing_dates(self) -> Self:
        """Reject incomplete or reversed report-date ranges."""
        has_start = self.date_from is not None
        has_end = self.date_to is not None
        if has_start != has_end:
            raise PydanticCustomError(
                INCOMPLETE_REPORT_RANGE, "report range requires both boundaries"
            )
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise PydanticCustomError(
                INVALID_REPORT_RANGE, "report range must be increasing"
            )
        return self
