"""Time and provenance contract for retained Vercel compatibility aliases."""

# ruff: noqa: D101, D103, EM101

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

from scripts.release_vercel_models import PROJECTS, TEAM_SLUG, ReleaseHoldError

if TYPE_CHECKING:
    from collections.abc import Mapping

RETENTION_DAYS = 31
RENEWAL_DAYS = 30
MAX_RECHECK_AGE = timedelta(minutes=5)
MAX_INITIAL_ANCHOR_LEAD = timedelta(hours=4)
EVIDENCE_SOURCE = "vercel-alias-ls-inspect"


@dataclass(frozen=True, slots=True)
class AliasRetentionProof:
    project_kind: Literal["api", "web"]
    alias: str
    deployment_id: str
    evidence_source: str
    rechecked_at: datetime
    retained_through: datetime
    renewal_recheck_at: datetime


@dataclass(frozen=True, slots=True)
class AliasRetentionObservation:
    project_kind: Literal["api", "web"]
    alias: str
    deployment_id: str
    project_name: str
    team_slug: str
    source_sha: str
    ready_state: str
    environment: str
    evidence_source: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class AliasRetentionExpectation:
    expected_kind: Literal["api", "web"]
    expected_alias: str
    alias_receipt: Mapping[str, object]
    cadence_anchor_at: datetime
    db_now: datetime


def parse_utc_timestamp(value: str, field: str) -> datetime:
    """Parse one timezone-aware UTC timestamp or fail closed."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        reason = f"{field}_invalid"
        raise ReleaseHoldError(reason) from error
    _require_utc(parsed, field)
    return parsed


def build_alias_retention_proof(
    *,
    observation: AliasRetentionObservation,
    alias_receipt: Mapping[str, object],
    cadence_anchor_at: datetime,
) -> AliasRetentionProof:
    """Bind a current Vercel alias observation to the retention schedule."""
    _require_utc(observation.observed_at, "alias_rechecked_at")
    _require_utc(cadence_anchor_at, "cadence_anchor_at")
    expected_name, _, _ = PROJECTS[observation.project_kind]
    if (
        observation.evidence_source != EVIDENCE_SOURCE
        or observation.project_name != expected_name
        or observation.team_slug != TEAM_SLUG
        or observation.ready_state != "READY"
        or observation.environment != "production"
    ):
        raise ReleaseHoldError("alias_retention_external_evidence")
    if (
        alias_receipt.get("alias") != observation.alias
        or alias_receipt.get("deployment_id") != observation.deployment_id
        or alias_receipt.get("source_sha") != observation.source_sha
        or alias_receipt.get("project_name") != observation.project_name
        or alias_receipt.get("team_slug") != observation.team_slug
    ):
        raise ReleaseHoldError("alias_retention_observation_mismatch")
    return AliasRetentionProof(
        project_kind=observation.project_kind,
        alias=observation.alias,
        deployment_id=observation.deployment_id,
        evidence_source=observation.evidence_source,
        rechecked_at=observation.observed_at,
        retained_through=cadence_anchor_at + timedelta(days=RETENTION_DAYS),
        renewal_recheck_at=cadence_anchor_at + timedelta(days=RENEWAL_DAYS),
    )


def validate_retention_proof(
    proof: AliasRetentionProof,
    expectation: AliasRetentionExpectation,
) -> bool:
    """Validate one fresh current or renewal recheck."""
    _validate_proof_scope(proof, expectation)
    return _validate_proof_times(proof, expectation)


def _validate_proof_scope(
    proof: AliasRetentionProof,
    expectation: AliasRetentionExpectation,
) -> None:
    if proof.evidence_source != EVIDENCE_SOURCE:
        raise ReleaseHoldError("alias_retention_external_evidence")
    if (
        proof.project_kind != expectation.expected_kind
        or proof.alias != expectation.expected_alias
    ):
        raise ReleaseHoldError("alias_retention_foreign_scope")
    if (
        expectation.alias_receipt.get("deployment_id") != proof.deployment_id
        or expectation.alias_receipt.get("alias") != proof.alias
    ):
        raise ReleaseHoldError("alias_retention_receipt_mismatch")


def _validate_proof_times(
    proof: AliasRetentionProof,
    expectation: AliasRetentionExpectation,
) -> bool:
    cadence_anchor_at = expectation.cadence_anchor_at
    db_now = expectation.db_now
    for value, field in (
        (cadence_anchor_at, "cadence_anchor_at"),
        (db_now, "db_now"),
        (proof.rechecked_at, "alias_rechecked_at"),
        (proof.retained_through, "alias_retained_through"),
        (proof.renewal_recheck_at, "renewal_recheck_at"),
    ):
        _require_utc(value, field)
    if cadence_anchor_at - db_now > MAX_INITIAL_ANCHOR_LEAD:
        raise ReleaseHoldError("cadence_anchor_external_future")
    age = db_now - proof.rechecked_at
    if age < timedelta(0):
        raise ReleaseHoldError("alias_recheck_from_future")
    if age >= MAX_RECHECK_AGE:
        raise ReleaseHoldError("alias_recheck_stale")
    required_until = cadence_anchor_at + timedelta(days=RETENTION_DAYS)
    renewal_at = cadence_anchor_at + timedelta(days=RENEWAL_DAYS)
    if proof.retained_through < required_until:
        raise ReleaseHoldError("alias_retention_too_short")
    if proof.renewal_recheck_at != renewal_at:
        raise ReleaseHoldError("alias_renewal_schedule_drift")
    if db_now >= proof.retained_through:
        raise ReleaseHoldError("alias_retention_expired")
    renewal_due = db_now >= renewal_at
    if renewal_due and proof.rechecked_at < renewal_at:
        raise ReleaseHoldError("alias_renewal_recheck_missing")
    return renewal_due


def format_utc(value: datetime) -> str:
    _require_utc(value, "timestamp")
    return value.isoformat().replace("+00:00", "Z")


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        reason = f"{field}_must_be_utc_aware"
        raise ReleaseHoldError(reason)


__all__ = (
    "AliasRetentionExpectation",
    "AliasRetentionObservation",
    "AliasRetentionProof",
    "build_alias_retention_proof",
    "format_utc",
    "parse_utc_timestamp",
    "validate_retention_proof",
)
