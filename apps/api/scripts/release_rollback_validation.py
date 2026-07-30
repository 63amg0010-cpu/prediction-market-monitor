"""Pure Matrix-B health and terminal rollback validation."""

# ruff: noqa: D103, EM101, TC003

from __future__ import annotations

from collections.abc import Mapping

from scripts.release_rollback_models import (
    DatabaseRollbackState,
    DeploymentState,
    MatrixBHealthInput,
    RollbackFinalizeInput,
    RollbackMutationIntent,
)
from scripts.release_vercel_models import (
    PROJECTS,
    TEAM_SLUG,
    ReleaseHoldError,
    seal_receipt,
    verify_receipt,
)

MATRIX_B_NODE_COUNT = 6


def _receipt(
    value: Mapping[str, object],
    request: MatrixBHealthInput | RollbackFinalizeInput,
) -> str:
    return verify_receipt(
        value,
        expected_sha=request.expected_sha,
        expected_plan_sha256=request.expected_plan_sha256,
        activation_nonce=request.activation_nonce,
    )


def _database(state: DatabaseRollbackState) -> None:
    if state.revision != "20260727_0010":
        raise ReleaseHoldError("matrix_b_wrong_database_revision")
    if state.latest_transition != "restore_writing":
        raise ReleaseHoldError("matrix_b_not_restore_writing")
    if state.manifold_enabled:
        raise ReleaseHoldError("matrix_b_manifold_enabled")
    if any(
        value is not None
        for value in (
            state.active_authorization_id,
            state.current_budget_id,
            state.current_binding_id,
            state.current_cadence_id,
        )
    ):
        raise ReleaseHoldError("matrix_b_active_pointer")
    if not state.zero_provider_binding:
        raise ReleaseHoldError("matrix_b_binding_not_zero_provider")
    if state.current_dcinside_binding_sha256 != state.original_dcinside_binding_sha256:
        raise ReleaseHoldError("matrix_b_dcinside_binding_not_restored")


def _deployment(state: DeploymentState, expected_sha: str) -> None:
    expected_name, _, _ = PROJECTS[state.project_kind]
    if state.project_name != expected_name or state.team_slug != TEAM_SLUG:
        raise ReleaseHoldError("matrix_b_foreign_deployment")
    if state.source_sha != expected_sha:
        raise ReleaseHoldError("matrix_b_wrong_deployment_sha")
    if state.ready_state != "READY" or state.environment != "production":
        raise ReleaseHoldError("matrix_b_deployment_not_ready")
    if state.alias != f"{expected_name}.vercel.app" or not state.alias_assigned:
        raise ReleaseHoldError("matrix_b_alias_failure")
    if state.no_op and not state.no_op_verified:
        raise ReleaseHoldError("matrix_b_unverified_noop")


def _edge(
    current: Mapping[str, object],
    predecessor_sha: str,
    reason: str,
) -> str:
    if current.get("predecessor_receipt_sha256") != predecessor_sha:
        raise ReleaseHoldError(reason)
    digest = current.get("receipt_sha256")
    if not isinstance(digest, str):
        raise ReleaseHoldError("missing_receipt_hash")
    return digest


def validate_matrix_b_health(
    request: MatrixBHealthInput,
) -> dict[str, object]:
    _database(request.database)
    _deployment(request.api, request.expected_sha)
    _deployment(request.web, request.expected_sha)
    downgrade_sha = _receipt(request.downgrade_receipt, request)
    if request.downgrade_receipt.get("command") not in {
        "migrate-0011-to-0010",
        "recover-operation-receipt",
    }:
        raise ReleaseHoldError("wrong_downgrade_receipt")
    binding_sha = _receipt(request.binding_restore_receipt, request)
    _ = _edge(
        request.binding_restore_receipt,
        downgrade_sha,
        "binding_restore_predecessor_mismatch",
    )
    api_sha = _receipt(request.api_receipt, request)
    _ = _edge(request.api_receipt, binding_sha, "api_predecessor_mismatch")
    web_sha = _receipt(request.web_receipt, request)
    _ = _edge(request.web_receipt, api_sha, "web_predecessor_mismatch")
    predecessor_sha = _receipt(request.predecessor_receipt, request)
    if predecessor_sha != web_sha:
        raise ReleaseHoldError("matrix_b_health_predecessor_mismatch")
    health = request.health
    if not all(
        (
            health.api_ok,
            health.web_ok,
            health.dcinside_ok,
            health.dcinside_search_ok,
        )
    ):
        raise ReleaseHoldError("matrix_b_health_failed")
    if health.manifold_results != 0:
        raise ReleaseHoldError("matrix_b_manifold_results_present")
    return seal_receipt(
        {
            "schema_version": 1,
            "command": "matrix-b-health",
            "reviewed_sha": request.expected_sha,
            "approved_plan_sha256": request.expected_plan_sha256,
            "activation_nonce": str(request.activation_nonce),
            "predecessor_receipt_sha256": predecessor_sha,
            "downgrade_receipt_sha256": downgrade_sha,
            "binding_restore_receipt_sha256": binding_sha,
            "api_receipt_sha256": api_sha,
            "web_receipt_sha256": web_sha,
            "database_revision": "20260727_0010",
            "state_before": "restore_writing",
            "state_after": "restore_writing",
            "aliases_ready": True,
            "dcinside_intact": True,
            "manifold_inert": True,
            "accepted": True,
        }
    )


def plan_rollback_finalize(
    request: RollbackFinalizeInput,
) -> RollbackMutationIntent:
    if request.incident_class != "technical":
        raise ReleaseHoldError("privacy_incident_requires_privacy_verify")
    _database(request.database)
    _deployment(request.api, request.expected_sha)
    _deployment(request.web, request.expected_sha)
    health_sha = _receipt(request.health_receipt, request)
    if (
        request.health_receipt.get("command") != "matrix-b-health"
        or request.health_receipt.get("state_after") != "restore_writing"
    ):
        raise ReleaseHoldError("invalid_matrix_b_health")
    chain_sha = _receipt(request.matrix_b_chain, request)
    if (
        request.matrix_b_chain.get("command") != "materialize-chain"
        or request.matrix_b_chain.get("manifest") != "matrix-b-chain-manifest.json"
        or request.matrix_b_chain.get("expected_terminal_command") != "matrix-b-health"
        or request.matrix_b_chain.get("terminal_receipt_sha256") != health_sha
        or request.matrix_b_chain.get("branch_complete") is not True
        or request.matrix_b_chain.get("node_count") != MATRIX_B_NODE_COUNT
        or request.matrix_b_chain.get("foreign_nodes") not in (0, None)
        or request.matrix_b_chain.get("duplicate_nodes") not in (0, None)
        or request.matrix_b_chain.get("extra_nodes") not in (0, None)
    ):
        raise ReleaseHoldError("invalid_matrix_b_chain")
    predecessor_sha = _receipt(request.predecessor_receipt, request)
    if predecessor_sha != chain_sha:
        raise ReleaseHoldError("rollback_finalize_predecessor_mismatch")
    body = {
        "schema_version": 1,
        "command": "rollback-finalize",
        "reviewed_sha": request.expected_sha,
        "approved_plan_sha256": request.expected_plan_sha256,
        "activation_nonce": str(request.activation_nonce),
        "predecessor_receipt_sha256": predecessor_sha,
        "matrix_b_chain_sha256": chain_sha,
        "incident_class": "technical",
        "expected_transition_id": request.database.latest_transition_id,
        "state_before": "restore_writing",
        "state_after": "restored",
        "accepted": True,
    }
    return RollbackMutationIntent(
        advisory_lock_namespace="source-binding",
        activation_nonce=request.activation_nonce,
        expected_latest_transition="restore_writing",
        expected_latest_transition_id=request.database.latest_transition_id,
        next_transition="restored",
        incident_class="technical",
        predecessor_receipt_sha256=predecessor_sha,
        matrix_b_chain_sha256=chain_sha,
        receipt_body=seal_receipt(body),
    )


__all__ = ("plan_rollback_finalize", "validate_matrix_b_health")
