"""Server-owned free-budget decisions for collection claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.domain.enums import BudgetDecisionStatus

from .base import CollectionError, CollectionErrorCode

SOFT_STOP_UNITS: Final = 70
HARD_STOP_UNITS: Final = 80
BUDGET_POLICY_VERSION: Final = "free-tier-70-80-v1"


@dataclass(frozen=True, slots=True)
class BudgetRecordFacts:
    """Persisted provider usage and reviewed no-spend thresholds."""

    observed_units: int
    soft_stop_units: int
    hard_stop_units: int
    paid_spend_enabled: bool


@dataclass(frozen=True, slots=True)
class ClaimBudgetDecision:
    """Effective source scope derived without client-supplied usage."""

    status: BudgetDecisionStatus
    reviewed_page_cap: int
    reviewed_post_cap: int
    skip_collection: bool
    reason_code: str


def derive_claim_budget(
    record: BudgetRecordFacts,
    *,
    reviewed_page_cap: int,
    reviewed_post_cap: int,
) -> ClaimBudgetDecision:
    """Apply the exact 70-unit reduction and 80-unit hard stop."""
    valid_policy = (
        record.soft_stop_units == SOFT_STOP_UNITS
        and record.hard_stop_units == HARD_STOP_UNITS
        and not record.paid_spend_enabled
        and record.observed_units >= 0
        and reviewed_page_cap > 0
        and reviewed_post_cap > 0
    )
    if not valid_policy:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    if record.observed_units >= record.hard_stop_units:
        return ClaimBudgetDecision(
            status=BudgetDecisionStatus.HARD_STOP,
            reviewed_page_cap=0,
            reviewed_post_cap=0,
            skip_collection=True,
            reason_code="free_tier_hard_stop",
        )
    if record.observed_units >= record.soft_stop_units:
        return ClaimBudgetDecision(
            status=BudgetDecisionStatus.SOFT_LIMIT,
            reviewed_page_cap=max(1, reviewed_page_cap // 2),
            reviewed_post_cap=max(1, reviewed_post_cap // 2),
            skip_collection=False,
            reason_code="free_tier_soft_scope_reduction",
        )
    return ClaimBudgetDecision(
        status=BudgetDecisionStatus.ALLOW,
        reviewed_page_cap=reviewed_page_cap,
        reviewed_post_cap=reviewed_post_cap,
        skip_collection=False,
        reason_code="free_tier_allow",
    )


__all__ = (
    "BUDGET_POLICY_VERSION",
    "BudgetRecordFacts",
    "ClaimBudgetDecision",
    "derive_claim_budget",
)
