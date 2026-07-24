"""Locked authorization and free-budget snapshots for collection claims."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.collection.adapters.models import SourceAuthorizationDecision
from app.db.operations_models import BudgetDecision, ProviderBudgetRecord

from .authorization import AuthorizationSnapshot, require_active_authorization
from .authorization_store import claim_authorization_statement
from .base import CollectionError, CollectionErrorCode, canonical_json_hash
from .claim_policy import (
    BUDGET_POLICY_VERSION,
    BudgetRecordFacts,
    derive_claim_budget,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.auth_models import CommunitySource
    from app.db.auth_models import SourceAuthorizationDecision as AuthRow
    from app.domain.enums import BudgetDecisionStatus
    from app.domain.types import JsonValue

    from .page_service_models import PageCommitServiceConfig


@dataclass(frozen=True, slots=True)
class ClaimSourcePolicy:
    """Exact authority and effective budget scope persisted on one run."""

    source_id: UUID
    authorization: SourceAuthorizationDecision
    budget_decision_id: UUID
    budget_status: BudgetDecisionStatus
    reviewed_page_cap: int
    reviewed_post_cap: int
    skip_budget_decision_id: UUID | None


async def claim_source_policies(
    session: AsyncSession,
    source_ids: tuple[UUID, ...],
    scope_version: str,
    now: datetime,
    config: PageCommitServiceConfig,
) -> dict[UUID, ClaimSourcePolicy]:
    """Lock, validate, and persist server-owned claim decisions."""
    rows = tuple(
        (
            await session.execute(
                claim_authorization_statement(source_ids, scope_version)
            )
        )
        .tuples()
        .all()
    )
    if len(rows) != len(source_ids) or len(set(source_ids)) != len(source_ids):
        raise CollectionError(CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403)
    policies: dict[UUID, ClaimSourcePolicy] = {}
    for source, authorization_row in rows:
        authorization = _authorization(source, authorization_row, now)
        budget_row = await _budget_record(session, source, now)
        decision = derive_claim_budget(
            BudgetRecordFacts(
                budget_row.observed_units,
                budget_row.soft_stop_units,
                budget_row.hard_stop_units,
                budget_row.paid_spend_enabled,
            ),
            reviewed_page_cap=config.reviewed_page_cap,
            reviewed_post_cap=config.reviewed_post_cap,
        )
        decision_id = uuid4()
        evidence: dict[str, JsonValue] = {
            "budget_record_id": str(budget_row.id),
            "observed_units": budget_row.observed_units,
            "policy_version": BUDGET_POLICY_VERSION,
            "reviewed_page_cap": decision.reviewed_page_cap,
            "reviewed_post_cap": decision.reviewed_post_cap,
            "source_id": str(source.id),
            "status": decision.status.value,
        }
        session.add(
            BudgetDecision(
                id=decision_id,
                budget_record_id=budget_row.id,
                source_id=source.id,
                status=decision.status,
                observed_units=budget_row.observed_units,
                reason_code=decision.reason_code,
                policy_version=BUDGET_POLICY_VERSION,
                reviewed_page_cap=decision.reviewed_page_cap,
                reviewed_post_cap=decision.reviewed_post_cap,
                evidence_sha256=canonical_json_hash(evidence),
                evidence_location=budget_row.evidence_location,
                decided_at=now,
            )
        )
        policies[source.id] = ClaimSourcePolicy(
            source.id,
            authorization,
            decision_id,
            decision.status,
            decision.reviewed_page_cap,
            decision.reviewed_post_cap,
            decision_id if decision.skip_collection else None,
        )
    return policies


def _authorization(
    source: CommunitySource,
    row: AuthRow,
    now: datetime,
) -> SourceAuthorizationDecision:
    snapshot = AuthorizationSnapshot(
        row.id,
        source.id,
        source.scope_version,
        source.enabled and source.active_authorization_id == row.id,
        row.status,
        row.effective_at,
        row.expires_at,
        row.revoked_at,
    )
    _ = require_active_authorization(snapshot, source.id, source.scope_version, now)
    if not isinstance(row.permitted_scope, dict):
        raise CollectionError(CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403)
    values: dict[str, JsonValue] = row.permitted_scope
    payload: dict[str, JsonValue] = {
        "decision_id": str(row.id),
        "source": source.platform.value,
        "status": row.status.value,
        "evidence_sha256": row.evidence_sha256,
        "evidence_location": row.evidence_location,
        "issuer": row.issuer,
        "reviewer": row.reviewer,
        "permitted_methods": values.get("permitted_methods", []),
        "permitted_routes": values.get("permitted_routes", []),
        "permitted_fields": values.get("permitted_fields", []),
        "permitted_subreddits": values.get("permitted_subreddits", []),
        "purpose": values.get("purpose", ""),
        "requests_per_minute": values.get("requests_per_minute", 0),
        "concurrency": values.get("concurrency", 0),
        "effective_at": row.effective_at.isoformat(),
        "expires_at": None if row.expires_at is None else row.expires_at.isoformat(),
        "revoked_at": None if row.revoked_at is None else row.revoked_at.isoformat(),
    }
    try:
        return SourceAuthorizationDecision.model_validate(payload)
    except ValidationError as error:
        raise CollectionError(
            CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403
        ) from error


async def _budget_record(
    session: AsyncSession,
    source: CommunitySource,
    now: datetime,
) -> ProviderBudgetRecord:
    row = (
        await session.execute(
            select(ProviderBudgetRecord)
            .where(
                ProviderBudgetRecord.provider == source.platform.value,
                ProviderBudgetRecord.billing_period_start <= now,
                ProviderBudgetRecord.billing_period_end > now,
            )
            .order_by(ProviderBudgetRecord.verified_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    return row


__all__ = ("ClaimSourcePolicy", "claim_source_policies")
