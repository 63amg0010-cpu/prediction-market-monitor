"""Typed, injected observations for the read-only Production release gate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProductionRequest:
    """Parser-owned arguments for ``production``."""

    database_url_env: str
    api_url: str
    web_url: str
    expected_sha: str
    expected_plan_sha256: str
    activation_nonce: str
    predecessor_receipt: Path
    expected_revision: str
    attestation: Path
    free_tier_result: Path
    release_chain: Path
    json_out: Path
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class ProductionProbeQuery:
    """Safe values passed to a separately injected live adapter."""

    database_url_env: str
    api_url: str
    web_url: str
    expected_sha: str
    expected_revision: str
    read_only: Literal[True] = True


@dataclass(frozen=True, slots=True)
class DeploymentProof:
    """One redacted Vercel deployment and health observation."""

    kind: Literal["api", "web"]
    project_name: str
    project_identity_sha256: str
    deployment_identity_sha256: str
    team_identity_sha256: str
    state: str
    production: bool
    reviewed_sha: str
    protected_identity_match: bool
    health_ok: bool
    health_database_backed: bool


@dataclass(frozen=True, slots=True)
class DatabaseProof:
    """Read-only durable database, activation, binding, and DCInside facts."""

    revision: str
    transaction_read_only: bool
    writes_observed: int
    reviewed_sha: str
    approved_plan_sha256: str
    activation_nonce: str
    release_chain_sha256: str
    attestation_sha256: str
    free_tier_sha256: str
    source_state: str
    source_enabled: bool
    binding_verified: bool
    source_id_sha256: str
    cadence_anchor_at: datetime
    authorization_expires_at: datetime
    dcinside_before_sha256: str
    dcinside_current_sha256: str
    dcinside_query_ok: bool
    dcinside_90d_count: int


@dataclass(frozen=True, slots=True)
class SearchProof:
    """Durable real-source literal, semantic, pagination, and freshness facts."""

    evidence_environment: str
    durable_database_backed: bool
    fixture_evidence: bool
    stub_evidence: bool
    intercepted: bool
    arbitrary_non_rule_literal: bool
    literal_sha256: str
    negative_literal_sha256: str
    positive_total: int
    positive_page_items: int
    positive_source_manifold_only: bool
    negative_total: int
    page: int
    page_size: int
    pagination_checked: bool
    pagination_deterministic: bool
    filters_preserved: bool
    keyword_total: int
    and_total: int
    keyword_unchanged: bool
    and_semantics: bool
    structured_identity_present: bool
    raw_provider_payload_present: bool
    freshness_visible: bool
    latest_manifold_at: datetime
    database_now: datetime
    dcinside_recent: bool
    cadence_complete: bool


@dataclass(frozen=True, slots=True)
class ProductionObservation:
    """All facts returned by exactly one injected observation."""

    deployments: tuple[DeploymentProof, ...]
    database: DatabaseProof
    search: SearchProof


class ProductionProbe(Protocol):
    """Adapter boundary; implementations may perform only read operations."""

    def observe(self, query: ProductionProbeQuery) -> ProductionObservation:
        """Return one schema-closed live observation without mutation."""
        ...


__all__ = (
    "DatabaseProof",
    "DeploymentProof",
    "ProductionObservation",
    "ProductionProbe",
    "ProductionProbeQuery",
    "ProductionRequest",
    "SearchProof",
)
