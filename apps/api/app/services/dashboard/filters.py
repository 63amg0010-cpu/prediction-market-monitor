"""Validated dashboard filter and pagination boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime  # noqa: TC003 - Pydantic resolves at runtime.
from typing import Annotated, ClassVar, Final, Literal, Self
from unicodedata import normalize
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
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
INVALID_SEARCH: Final = "invalid_search"
_ASCII_EDGE_WHITESPACE: Final = "\t\n\v\f\r "
_ASCII_UPPER: Final = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_ASCII_LOWER: Final = "abcdefghijklmnopqrstuvwxyz"
_ASCII_FOLD_TABLE: Final = str.maketrans(_ASCII_UPPER, _ASCII_LOWER)
_MIN_SEARCH_SCALARS: Final = 2
_MAX_SEARCH_SCALARS: Final = 100
_MIN_SURROGATE: Final = 0xD800
_MAX_SURROGATE: Final = 0xDFFF
_SCALAR_COUNT_REASON: Final = "scalar_count"
_UNICODE_SCALAR_REASON: Final = "unicode_scalar"
SearchFoldReason = Literal["scalar_count", "unicode_scalar"]


@dataclass(frozen=True, slots=True)
class SearchFoldResult:
    """Canonical folded search text and its Unicode-scalar count."""

    value: str
    scalar_count: int


class SearchFoldError(Exception):
    """Stable invalid-search reason shared by boundary adapters."""

    reason: SearchFoldReason

    def __init__(self, reason: SearchFoldReason) -> None:
        """Create an error without retaining rejected user text."""
        self.reason = reason
        super().__init__(reason)


def search_fold_v1(value: str) -> SearchFoldResult:
    """Apply the versioned locale-independent search validation fold."""
    if any(
        _MIN_SURROGATE <= ord(character) <= _MAX_SURROGATE for character in value
    ):
        raise SearchFoldError(_UNICODE_SCALAR_REASON)
    folded = normalize("NFC", value.strip(_ASCII_EDGE_WHITESPACE)).translate(
        _ASCII_FOLD_TABLE
    )
    scalar_count = len(folded)
    if scalar_count < _MIN_SEARCH_SCALARS or scalar_count > _MAX_SEARCH_SCALARS:
        raise SearchFoldError(_SCALAR_COUNT_REASON)
    return SearchFoldResult(value=folded, scalar_count=scalar_count)


def search_like_pattern_v1(folded_value: str) -> str:
    """Escape a folded literal once for a bound PostgreSQL LIKE pattern."""
    escaped = (
        folded_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


class DashboardFilters(BaseModel):
    """Country, source, keyword, and aware UTC window filters."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    country: Country | None = None
    source_id: UUID | None = None
    keyword: Keyword | None = None
    search: str | None = None
    published_from: datetime | None = None
    published_to: datetime | None = None

    @field_validator("search", mode="before")
    @classmethod
    def parse_search(cls, value: str | None) -> str | None:
        """Parse raw API search text into the canonical internal value."""
        if value is None:
            return None
        try:
            return search_fold_v1(value).value
        except SearchFoldError as exc:
            raise PydanticCustomError(
                INVALID_SEARCH,
                "search must contain 2 to 100 Unicode scalar values",
                {"reason": exc.reason},
            ) from exc

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
