"""Typed read models and mutation intent for Matrix-B finalization."""

# ruff: noqa: D101, TC003

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal
from uuid import UUID


@dataclass(frozen=True, slots=True)
class DatabaseRollbackState:
    revision: str
    latest_transition: str
    latest_transition_id: int
    manifold_enabled: bool
    active_authorization_id: UUID | None
    current_budget_id: UUID | None
    current_binding_id: UUID | None
    current_cadence_id: UUID | None
    original_dcinside_binding_sha256: str
    current_dcinside_binding_sha256: str
    zero_provider_binding: bool


@dataclass(frozen=True, slots=True)
class DeploymentState:
    project_kind: Literal["api", "web"]
    project_name: str
    team_slug: str
    source_sha: str
    ready_state: str
    environment: str
    alias: str
    alias_assigned: bool
    no_op: bool = False
    no_op_verified: bool = False


@dataclass(frozen=True, slots=True)
class HealthState:
    api_ok: bool
    web_ok: bool
    dcinside_ok: bool
    dcinside_search_ok: bool
    manifold_results: int


@dataclass(frozen=True, slots=True)
class MatrixBHealthInput:
    database: DatabaseRollbackState
    api: DeploymentState
    web: DeploymentState
    health: HealthState
    downgrade_receipt: Mapping[str, object]
    binding_restore_receipt: Mapping[str, object]
    api_receipt: Mapping[str, object]
    web_receipt: Mapping[str, object]
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: UUID
    predecessor_receipt: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RollbackFinalizeInput:
    incident_class: Literal["technical", "privacy", "authorization"]
    database: DatabaseRollbackState
    api: DeploymentState
    web: DeploymentState
    health_receipt: Mapping[str, object]
    matrix_b_chain: Mapping[str, object]
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: UUID
    predecessor_receipt: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RollbackMutationIntent:
    """Foundation-owned single-transaction CAS, not a performed DB write."""

    advisory_lock_namespace: Literal["source-binding"]
    activation_nonce: UUID
    expected_latest_transition: Literal["restore_writing"]
    expected_latest_transition_id: int
    next_transition: Literal["restored"]
    incident_class: Literal["technical"]
    predecessor_receipt_sha256: str
    matrix_b_chain_sha256: str
    receipt_body: Mapping[str, object]


__all__ = (
    "DatabaseRollbackState",
    "DeploymentState",
    "HealthState",
    "MatrixBHealthInput",
    "RollbackFinalizeInput",
    "RollbackMutationIntent",
)
