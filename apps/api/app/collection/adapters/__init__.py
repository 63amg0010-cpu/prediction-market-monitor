"""Fail-closed source adapter contracts and implementations."""

from .base import SourceAdapter
from .models import (
    AdapterPage,
    BlockedKind,
    NormalizedPost,
    PreflightBlocked,
    PreflightContext,
    PreflightReady,
    RejectedOversize,
    SourceAuthorizationDecision,
    SourceBlockedError,
)

__all__ = [
    "AdapterPage",
    "BlockedKind",
    "NormalizedPost",
    "PreflightBlocked",
    "PreflightContext",
    "PreflightReady",
    "RejectedOversize",
    "SourceAdapter",
    "SourceAuthorizationDecision",
    "SourceBlockedError",
]
