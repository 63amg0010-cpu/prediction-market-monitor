from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.collection.adapters.models import (
    BlockedFetchRequest,
    BlockedKind,
    PreflightBlocked,
    PreflightContext,
    SourceBlockedError,
)
from app.collection.adapters.naver_finance import NaverFinanceAdapter
from app.collection.adapters.toss_securities import TossSecuritiesAdapter
from app.domain.enums import SourcePlatform

NOW = datetime(2026, 7, 21, 6, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("adapter", "kind", "code"),
    [
        (
            NaverFinanceAdapter(),
            BlockedKind.BLOCKED_POLICY,
            "robots_and_automation_policy_block",
        ),
        (
            TossSecuritiesAdapter(),
            BlockedKind.BLOCKED_AUTHORIZATION,
            "official_community_api_authorization_missing",
        ),
    ],
)
def test_current_source_evidence_blocks_preflight(
    adapter: NaverFinanceAdapter | TossSecuritiesAdapter,
    kind: BlockedKind,
    code: str,
) -> None:
    # Given
    context = PreflightContext(authorization=None, checked_at=NOW)

    # When
    result = adapter.preflight(context)

    # Then
    assert isinstance(result, PreflightBlocked)
    assert result.kind is kind
    assert result.code == code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "adapter",
    [NaverFinanceAdapter(), TossSecuritiesAdapter()],
)
async def test_blocked_adapters_have_no_fetch_or_circumvention_path(
    adapter: NaverFinanceAdapter | TossSecuritiesAdapter,
) -> None:
    # Given
    request = BlockedFetchRequest(
        preflight=PreflightContext(authorization=None, checked_at=NOW)
    )

    # When / Then
    with pytest.raises(SourceBlockedError):
        _ = await adapter.fetch_page(request)


@pytest.mark.parametrize("adapter", [NaverFinanceAdapter(), TossSecuritiesAdapter()])
def test_toss_and_naver_exclusivity_blocks_simultaneous_selection(
    adapter: NaverFinanceAdapter | TossSecuritiesAdapter,
) -> None:
    # Given
    context = PreflightContext(
        authorization=None,
        checked_at=NOW,
        enabled_finance_sources=frozenset(
            {SourcePlatform.NAVER_FINANCE, SourcePlatform.TOSS_SECURITIES}
        ),
    )

    # When
    result = adapter.preflight(context)

    # Then
    assert isinstance(result, PreflightBlocked)
    assert result.kind is BlockedKind.BLOCKED_POLICY
    assert result.code == "finance_exclusivity_violation"
