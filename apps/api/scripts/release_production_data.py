"""Durable database and real-search proof checks for Production."""

# ruff: noqa: EM101, PLR2004

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from .release_chain_common import Bindings, ReleaseChainError

if TYPE_CHECKING:
    from datetime import datetime

    from .release_production_evidence import EvidenceDigests
    from .release_production_models import ProductionObservation, ProductionRequest

REVISION = "20260727_0011"
HEX = frozenset("0123456789abcdef")


def validate_database_and_search(
    observation: ProductionObservation,
    request: ProductionRequest,
    bindings: Bindings,
    evidence: EvidenceDigests,
    release_chain_sha256: str,
) -> None:
    """Require exact 0011 activation state and real durable search behavior."""
    database = observation.database
    if database.revision != REVISION or database.revision != request.expected_revision:
        raise ReleaseChainError("database_revision_mismatch")
    if not database.transaction_read_only or database.writes_observed != 0:
        raise ReleaseChainError("production_probe_writable")
    actual = (
        database.reviewed_sha,
        database.approved_plan_sha256,
        database.activation_nonce,
        database.release_chain_sha256,
        database.attestation_sha256,
        database.free_tier_sha256,
    )
    expected = (
        bindings.reviewed_sha,
        bindings.approved_plan_sha256,
        bindings.activation_nonce,
        release_chain_sha256,
        evidence.attestation_sha256,
        evidence.free_tier_sha256,
    )
    if actual != expected:
        raise ReleaseChainError("durable_binding_mismatch")
    if (
        database.source_state != "active"
        or not database.source_enabled
        or not database.binding_verified
        or not _hex(database.source_id_sha256)
    ):
        raise ReleaseChainError("manifold_not_active")
    _aware(database.cadence_anchor_at)
    _aware(database.authorization_expires_at)
    if (
        database.authorization_expires_at
        != database.cadence_anchor_at + timedelta(days=31)
    ):
        raise ReleaseChainError("source_anchor_binding_mismatch")
    if (
        not database.dcinside_query_ok
        or database.dcinside_90d_count < 0
        or not _hex(database.dcinside_before_sha256)
        or database.dcinside_before_sha256 != database.dcinside_current_sha256
    ):
        raise ReleaseChainError("dcinside_changed")
    _validate_search(observation)


def _validate_search(observation: ProductionObservation) -> None:
    value = observation.search
    if (
        value.evidence_environment != "production"
        or not value.durable_database_backed
        or value.fixture_evidence
        or value.stub_evidence
        or value.intercepted
    ):
        raise ReleaseChainError("nonproduction_evidence")
    if (
        not value.arbitrary_non_rule_literal
        or not _hex(value.literal_sha256)
        or not _hex(value.negative_literal_sha256)
        or value.literal_sha256 == value.negative_literal_sha256
        or value.positive_total < 1
        or not 1 <= value.positive_page_items <= min(50, value.positive_total)
        or not value.positive_source_manifold_only
    ):
        raise ReleaseChainError("literal_positive_missing")
    if value.negative_total != 0:
        raise ReleaseChainError("literal_negative_matched")
    if (
        value.page != 1
        or value.page_size != 50
        or not value.pagination_checked
        or not value.pagination_deterministic
        or not value.filters_preserved
    ):
        raise ReleaseChainError("pagination_contract_failed")
    if (
        value.keyword_total < 1
        or value.and_total < 1
        or value.and_total > min(value.keyword_total, value.positive_total)
        or not value.keyword_unchanged
        or not value.and_semantics
    ):
        raise ReleaseChainError("keyword_and_contract_failed")
    if value.structured_identity_present or value.raw_provider_payload_present:
        raise ReleaseChainError("identity_data_present")
    _aware(value.database_now)
    _aware(value.latest_manifold_at)
    age = value.database_now - value.latest_manifold_at
    if not value.freshness_visible or age < timedelta(0) or age >= timedelta(days=30):
        raise ReleaseChainError("freshness_contract_failed")


def _hex(value: str) -> bool:
    return len(value) == 64 and set(value) <= HEX


def _aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ReleaseChainError("production_time_not_timezone_aware")


__all__ = ("validate_database_and_search",)
