"""Current authorization and provider-backed skip proof persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.collection.adapters.models import SourceAuthorizationDecision as AuthSnapshot
from app.db.auth_models import CommunitySource, SourceAuthorizationDecision
from app.db.operations_models import BudgetDecision, ProviderBudgetRecord
from app.domain.enums import AuthorizationStatus, BudgetDecisionStatus

from .authorization import AuthorizationSnapshot, require_active_authorization
from .base import CollectionError, CollectionErrorCode
from .claim_policy import (
    BUDGET_POLICY_VERSION,
    BudgetRecordFacts,
    derive_claim_budget,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.run_models import CollectionRun


@dataclass(frozen=True, slots=True)
class SkipProofContext:
    """Locked rows and normalized evidence used for one proof mutation."""

    session: AsyncSession
    source: CommunitySource
    run: CollectionRun
    now: datetime
    evidence_sha256: str
    evidence_location: str


async def current_authorization(
    session: AsyncSession, run: CollectionRun, now: datetime
) -> tuple[CommunitySource, SourceAuthorizationDecision]:
    """Lock and return only the run's still-current approved decision."""
    source = (
        await session.execute(
            select(CommunitySource)
            .where(CommunitySource.id == run.source_id)
            .with_for_update()
        )
    ).scalar_one()
    decision = (
        await session.execute(
            select(SourceAuthorizationDecision)
            .where(
                SourceAuthorizationDecision.id == source.active_authorization_id,
                SourceAuthorizationDecision.source_id == source.id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if decision is None or decision.id != run.authorization_decision_id:
        raise CollectionError(CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403)
    _ = require_active_authorization(
        AuthorizationSnapshot(
            decision.id,
            source.id,
            source.scope_version,
            source.enabled and source.active_authorization_id == decision.id,
            decision.status,
            decision.effective_at,
            decision.expires_at,
            decision.revoked_at,
        ),
        run.source_id,
        run.scope_version,
        now,
    )
    return source, decision


def attach_policy_proof(
    context: SkipProofContext,
    current: SourceAuthorizationDecision,
) -> UUID:
    """Append a server-derived revocation proof without mutating past evidence."""
    decision_id = uuid4()
    context.session.add(
        SourceAuthorizationDecision(
            id=decision_id,
            source_id=context.source.id,
            status=AuthorizationStatus.REVOKED,
            evidence_sha256=context.evidence_sha256,
            evidence_location=context.evidence_location,
            issuer="monitor-control-plane",
            reviewer="provider-policy-v1",
            permitted_scope=current.permitted_scope,
            effective_at=context.now,
            expires_at=current.expires_at,
            revoked_at=context.now,
            decided_at=context.now,
        )
    )
    context.source.enabled = False
    context.source.active_authorization_id = decision_id
    context.run.skip_authorization_decision_id = decision_id
    return decision_id


async def attach_quota_proof(context: SkipProofContext) -> UUID:
    """Promote 429 only when a current reviewed free-budget period backs it."""
    current = (
        await context.session.execute(
            select(BudgetDecision)
            .where(
                BudgetDecision.id == context.run.budget_decision_id,
                BudgetDecision.source_id == context.run.source_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if current is None:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    record = (
        await context.session.execute(
            select(ProviderBudgetRecord)
            .where(ProviderBudgetRecord.id == current.budget_record_id)
            .with_for_update()
        )
    ).scalar_one()
    current_period = (
        record.provider == context.source.platform.value
        and record.billing_period_start <= context.now < record.billing_period_end
    )
    if not current_period:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    decision = derive_claim_budget(
        BudgetRecordFacts(
            record.observed_units,
            record.soft_stop_units,
            record.hard_stop_units,
            record.paid_spend_enabled,
        ),
        reviewed_page_cap=max(1, current.reviewed_page_cap),
        reviewed_post_cap=max(1, current.reviewed_post_cap),
    )
    if decision.status is not BudgetDecisionStatus.HARD_STOP:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    decision_id = uuid4()
    context.session.add(
        BudgetDecision(
            id=decision_id,
            budget_record_id=record.id,
            source_id=context.source.id,
            status=BudgetDecisionStatus.HARD_STOP,
            observed_units=record.observed_units,
            reason_code="provider_429_hard_stop",
            policy_version=f"{BUDGET_POLICY_VERSION}+provider-429-v1",
            reviewed_page_cap=0,
            reviewed_post_cap=0,
            evidence_sha256=context.evidence_sha256,
            evidence_location=context.evidence_location,
            decided_at=context.now,
        )
    )
    context.run.skip_budget_decision_id = decision_id
    return decision_id


def run_authorization(run: CollectionRun) -> AuthSnapshot:
    """Parse the immutable claim snapshot or fail closed."""
    try:
        return AuthSnapshot.model_validate(run.authorization_snapshot)
    except ValidationError as error:
        raise CollectionError(
            CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403
        ) from error


__all__ = (
    "SkipProofContext",
    "attach_policy_proof",
    "attach_quota_proof",
    "current_authorization",
    "run_authorization",
)
