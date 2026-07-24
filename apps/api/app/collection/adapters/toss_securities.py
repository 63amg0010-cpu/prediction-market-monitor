"""Explicitly blocked Toss Securities community adapter."""

from typing import ClassVar

from app.domain.enums import SourcePlatform

from .blocked import EvidenceBlockedAdapter
from .models import BlockedKind


class TossSecuritiesAdapter(EvidenceBlockedAdapter):
    """Remain disabled until an official community-data route is approved."""

    platform: ClassVar[SourcePlatform] = SourcePlatform.TOSS_SECURITIES
    blocked_kind: ClassVar[BlockedKind] = BlockedKind.BLOCKED_AUTHORIZATION
    blocked_code: ClassVar[str] = "official_community_api_authorization_missing"
