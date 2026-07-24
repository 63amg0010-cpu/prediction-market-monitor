from __future__ import annotations

from app.collection.claim_policy import BudgetRecordFacts, derive_claim_budget
from app.domain.enums import BudgetDecisionStatus


def test_claim_budget_reduces_scope_at_seventy_percent() -> None:
    # Given
    record = BudgetRecordFacts(
        observed_units=70,
        soft_stop_units=70,
        hard_stop_units=80,
        paid_spend_enabled=False,
    )

    # When
    decision = derive_claim_budget(record, reviewed_page_cap=4, reviewed_post_cap=20)

    # Then
    assert decision.status is BudgetDecisionStatus.SOFT_LIMIT
    assert decision.reviewed_page_cap == 2
    assert decision.reviewed_post_cap == 10
    assert decision.skip_collection is False


def test_claim_budget_hard_stops_at_eighty_percent() -> None:
    # Given
    record = BudgetRecordFacts(
        observed_units=80,
        soft_stop_units=70,
        hard_stop_units=80,
        paid_spend_enabled=False,
    )

    # When
    decision = derive_claim_budget(record, reviewed_page_cap=4, reviewed_post_cap=20)

    # Then
    assert decision.status is BudgetDecisionStatus.HARD_STOP
    assert decision.reviewed_page_cap == 0
    assert decision.reviewed_post_cap == 0
    assert decision.skip_collection is True
