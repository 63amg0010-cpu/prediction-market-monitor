"""Exact free-tier budget state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Final

from .errors import invariant

DEFAULT_SOFT_LIMIT: Final[Decimal] = Decimal("0.70")
DEFAULT_HARD_LIMIT: Final[Decimal] = Decimal("0.80")


class BudgetState(StrEnum):
    """Three-state free-budget decision."""

    NORMAL = "normal"
    SOFT_LIMITED = "soft_limited"
    HARD_BLOCKED = "hard_blocked"


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """Immutable budget decision and utilization evidence."""

    state: BudgetState
    used: Decimal
    quota: Decimal
    utilization: Decimal
    soft_limit: Decimal
    hard_limit: Decimal

    @property
    def allows_collection(self) -> bool:
        """Return whether new collection is permitted."""
        return self.state is not BudgetState.HARD_BLOCKED

    @property
    def reduce_scope(self) -> bool:
        """Return whether the soft-limit scope reduction applies."""
        return self.state is BudgetState.SOFT_LIMITED


def evaluate_free_budget(
    used: int | Decimal,
    quota: int | Decimal,
    *,
    soft_limit: Decimal = DEFAULT_SOFT_LIMIT,
    hard_limit: Decimal = DEFAULT_HARD_LIMIT,
) -> BudgetDecision:
    """Classify usage at exact soft and hard threshold boundaries."""
    used_value = Decimal(str(used))
    quota_value = Decimal(str(quota))
    if used_value < 0:
        invariant("budget_negative", "used", "usage cannot be negative")
    if quota_value <= 0:
        invariant("budget_quota_invalid", "quota", "quota must be positive")
    if not Decimal(0) < soft_limit < hard_limit <= Decimal(1):
        invariant(
            "budget_threshold_invalid",
            "budget_policy",
            "soft limit must be below hard limit and both must be in (0, 1]",
        )
    utilization = used_value / quota_value
    state = (
        BudgetState.HARD_BLOCKED
        if utilization >= hard_limit
        else BudgetState.SOFT_LIMITED
        if utilization >= soft_limit
        else BudgetState.NORMAL
    )
    return BudgetDecision(
        state=state,
        used=used_value,
        quota=quota_value,
        utilization=utilization,
        soft_limit=soft_limit,
        hard_limit=hard_limit,
    )
