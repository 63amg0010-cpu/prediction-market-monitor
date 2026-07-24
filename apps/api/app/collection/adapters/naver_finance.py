"""Explicitly blocked Naver Finance community adapter."""

from typing import ClassVar

from app.domain.enums import SourcePlatform

from .blocked import EvidenceBlockedAdapter
from .models import BlockedKind


class NaverFinanceAdapter(EvidenceBlockedAdapter):
    """Expose the reviewed robots and automation-policy denial."""

    platform: ClassVar[SourcePlatform] = SourcePlatform.NAVER_FINANCE
    blocked_kind: ClassVar[BlockedKind] = BlockedKind.BLOCKED_POLICY
    blocked_code: ClassVar[str] = "robots_and_automation_policy_block"
