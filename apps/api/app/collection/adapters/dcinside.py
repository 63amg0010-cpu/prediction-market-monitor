"""Explicitly blocked DCInside adapter."""

from typing import ClassVar

from app.domain.enums import SourcePlatform

from .blocked import EvidenceBlockedAdapter
from .models import BlockedKind


class DCInsideAdapter(EvidenceBlockedAdapter):
    """Remain disabled until a written reviewed collection route exists."""

    platform: ClassVar[SourcePlatform] = SourcePlatform.DCINSIDE
    blocked_kind: ClassVar[BlockedKind] = BlockedKind.BLOCKED_AUTHORIZATION
    blocked_code: ClassVar[str] = "written_reviewed_route_missing"
