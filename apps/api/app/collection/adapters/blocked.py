"""Fail-closed base for sources without a reviewed collection route."""

from __future__ import annotations

from typing import ClassVar, Final

from app.domain.enums import SourcePlatform

from .models import (
    AdapterPage,
    BlockedFetchRequest,
    BlockedKind,
    PreflightBlocked,
    PreflightContext,
    SourceBlockedError,
)

_FINANCE_ALTERNATIVES: Final = frozenset(
    {SourcePlatform.NAVER_FINANCE, SourcePlatform.TOSS_SECURITIES}
)


class EvidenceBlockedAdapter:
    """Expose an explicit denial while no reviewed provider route exists."""

    platform: ClassVar[SourcePlatform]
    blocked_kind: ClassVar[BlockedKind]
    blocked_code: ClassVar[str]

    @property
    def source(self) -> SourcePlatform:
        """Return the blocked provider identity."""
        return self.platform

    def preflight(self, context: PreflightContext) -> PreflightBlocked:
        """Return the current evidence denial without consulting credentials."""
        if self.platform in _FINANCE_ALTERNATIVES and _FINANCE_ALTERNATIVES.issubset(
            context.enabled_finance_sources
        ):
            return PreflightBlocked(
                kind=BlockedKind.BLOCKED_POLICY,
                code="finance_exclusivity_violation",
            )
        return PreflightBlocked(kind=self.blocked_kind, code=self.blocked_code)

    async def fetch_page(self, request: BlockedFetchRequest) -> AdapterPage:
        """Fail before any source-network operation can be constructed."""
        raise SourceBlockedError(self.source, self.preflight(request.preflight))
