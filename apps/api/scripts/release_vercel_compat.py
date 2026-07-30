"""Read-only compatibility-state validation after the two initial deploys."""

# ruff: noqa: D101, EM101, TC003

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from scripts.release_vercel_models import (
    PROJECTS,
    TEAM_SLUG,
    ReleaseHoldError,
    seal_receipt,
    verify_receipt,
)
from scripts.release_vercel_retention import (
    AliasRetentionExpectation,
    AliasRetentionProof,
    format_utc,
    validate_retention_proof,
)

if TYPE_CHECKING:
    from scripts.release_rollback_models import DeploymentState, HealthState


@dataclass(frozen=True, slots=True)
class CompatibilityDatabaseState:
    revision: str
    manifold_rows: int
    manifold_enabled: bool
    active_pointer_count: int


@dataclass(frozen=True, slots=True)
class CompatibilityStateInput:
    database: CompatibilityDatabaseState
    api: DeploymentState
    web: DeploymentState
    health: HealthState
    api_claim_endpoint_compatible: bool
    api_evidence_endpoint_compatible: bool
    api_receipt: Mapping[str, object]
    web_receipt: Mapping[str, object]
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: UUID
    predecessor_receipt: Mapping[str, object]
    cadence_anchor_at: datetime
    db_now: datetime
    api_retention: AliasRetentionProof
    web_retention: AliasRetentionProof


def _deployment(state: DeploymentState, expected_sha: str) -> None:
    expected_name, _, _ = PROJECTS[state.project_kind]
    if state.project_name != expected_name or state.team_slug != TEAM_SLUG:
        raise ReleaseHoldError("compat_foreign_deployment")
    if state.source_sha != expected_sha:
        raise ReleaseHoldError("compat_wrong_deployment_sha")
    if state.ready_state != "READY" or state.environment != "production":
        raise ReleaseHoldError("compat_deployment_not_ready")
    expected_alias = f"{expected_name}-fresh-search-compat.vercel.app"
    if state.alias != expected_alias or not state.alias_assigned:
        raise ReleaseHoldError("compat_alias_failure")


def validate_compat_state(
    request: CompatibilityStateInput,
) -> dict[str, object]:
    """Prove DB-0010 compatibility without changing DB or provider state."""
    database = request.database
    if (
        database.revision != "20260727_0010"
        or database.manifold_rows != 0
        or database.manifold_enabled
        or database.active_pointer_count != 0
    ):
        raise ReleaseHoldError("compat_database_not_inert_0010")
    _deployment(request.api, request.expected_sha)
    _deployment(request.web, request.expected_sha)
    api_sha = verify_receipt(
        request.api_receipt,
        expected_sha=request.expected_sha,
        expected_plan_sha256=request.expected_plan_sha256,
        activation_nonce=request.activation_nonce,
    )
    web_sha = verify_receipt(
        request.web_receipt,
        expected_sha=request.expected_sha,
        expected_plan_sha256=request.expected_plan_sha256,
        activation_nonce=request.activation_nonce,
    )
    if request.web_receipt.get("predecessor_receipt_sha256") != api_sha:
        raise ReleaseHoldError("compat_split_deploy_chain")
    predecessor_sha = verify_receipt(
        request.predecessor_receipt,
        expected_sha=request.expected_sha,
        expected_plan_sha256=request.expected_plan_sha256,
        activation_nonce=request.activation_nonce,
    )
    if predecessor_sha != web_sha:
        raise ReleaseHoldError("compat_predecessor_mismatch")
    if not (
        request.api_claim_endpoint_compatible
        and request.api_evidence_endpoint_compatible
    ):
        raise ReleaseHoldError("compat_api_endpoint_missing")
    health = request.health
    if (
        not all(
            (
                health.api_ok,
                health.web_ok,
                health.dcinside_ok,
                health.dcinside_search_ok,
            )
        )
        or health.manifold_results != 0
    ):
        raise ReleaseHoldError("compat_health_failed")
    api_renewal = validate_retention_proof(
        request.api_retention,
        AliasRetentionExpectation(
            expected_kind="api",
            expected_alias=request.api.alias,
            alias_receipt=request.api_receipt,
            cadence_anchor_at=request.cadence_anchor_at,
            db_now=request.db_now,
        ),
    )
    web_renewal = validate_retention_proof(
        request.web_retention,
        AliasRetentionExpectation(
            expected_kind="web",
            expected_alias=request.web.alias,
            alias_receipt=request.web_receipt,
            cadence_anchor_at=request.cadence_anchor_at,
            db_now=request.db_now,
        ),
    )
    retained_through = min(
        request.api_retention.retained_through,
        request.web_retention.retained_through,
    )
    return seal_receipt(
        {
            "schema_version": 1,
            "command": "compat-state",
            "reviewed_sha": request.expected_sha,
            "approved_plan_sha256": request.expected_plan_sha256,
            "activation_nonce": str(request.activation_nonce),
            "predecessor_receipt_sha256": predecessor_sha,
            "api_receipt_sha256": api_sha,
            "web_receipt_sha256": web_sha,
            "database_revision": "20260727_0010",
            "claim_endpoint_compatible": True,
            "evidence_endpoint_compatible": True,
            "aliases_ready": True,
            "dcinside_intact": True,
            "manifold_inert": True,
            "cadence_anchor_at": format_utc(request.cadence_anchor_at),
            "alias_retained_through": format_utc(retained_through),
            "renewal_recheck_at": format_utc(request.api_retention.renewal_recheck_at),
            "retention_rechecked_at": format_utc(request.db_now),
            "renewal_recheck_satisfied": api_renewal and web_renewal,
            "accepted": True,
        }
    )


__all__ = (
    "AliasRetentionProof",
    "CompatibilityDatabaseState",
    "CompatibilityStateInput",
    "validate_compat_state",
)
