"""Structural source adapter interface."""

from typing import Protocol

from app.domain.enums import SourcePlatform

from .http_errors import HttpFailure, HttpFailureClassification
from .models import AdapterPage, NormalizedItem, PreflightContext, PreflightResult


class SourceAdapter[FetchRequestT, RawItemT](Protocol):
    """Typed collection seam implemented by every provider adapter."""

    @property
    def source(self) -> SourcePlatform:
        """Return the provider identity."""
        ...

    def preflight(self, context: PreflightContext) -> PreflightResult:
        """Evaluate current authorization before a provider operation."""
        ...

    async def fetch_page(self, request: FetchRequestT) -> AdapterPage:
        """Fetch and normalize one bounded provider page."""
        ...

    def normalize(self, raw: RawItemT) -> NormalizedItem:
        """Remove disallowed fields and retain the accepted original."""
        ...

    def next_checkpoint(self, page: AdapterPage) -> str | None:
        """Return the opaque provider cursor for page-commit CAS."""
        ...

    def classify_error(self, failure: HttpFailure) -> HttpFailureClassification:
        """Map an HTTP failure to a control-plane outcome."""
        ...
