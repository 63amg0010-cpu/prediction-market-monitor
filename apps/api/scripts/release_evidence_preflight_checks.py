"""Pure completeness, freshness, and no-spend checks."""

# ruff: noqa: EM101, TC003

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from scripts.release_evidence_contracts import (
    PROVIDER_PLANS,
    PROVIDERS,
    EvidenceHoldError,
)
from scripts.release_evidence_graph import canonical_bytes, receipt_sha256

MAX_AGE = timedelta(hours=2)
THRESHOLD = 0.70


def require_bindings(
    value: Mapping[str, object],
    *,
    reviewed_sha: str,
    plan_sha: str,
    activation_nonce: UUID,
) -> None:
    """Require the three public release bindings."""
    plan = value.get(
        "approved_plan_sha256",
        value.get("expected_plan_sha256"),
    )
    if (
        value.get("reviewed_sha") != reviewed_sha
        or plan != plan_sha
        or value.get("activation_nonce") != str(activation_nonce)
    ):
        raise EvidenceHoldError("evidence_binding_mismatch")


def evidence_time(value: object) -> datetime:
    """Parse a timezone-aware evidence timestamp."""
    if not isinstance(value, str):
        raise EvidenceHoldError("evidence_time_missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise EvidenceHoldError("evidence_time_invalid") from error
    if parsed.tzinfo is None:
        raise EvidenceHoldError("evidence_time_timezone_required")
    return parsed.astimezone(UTC)


def require_fresh(value: Mapping[str, object], db_now: datetime) -> None:
    """Enforce the exact exclusive two-hour age boundary."""
    captured_at = evidence_time(value.get("captured_at"))
    if captured_at > db_now or db_now >= captured_at + MAX_AGE:
        raise EvidenceHoldError("evidence_stale")


def require_provider_captures(
    captures: Sequence[Mapping[str, object]],
    *,
    reviewed_sha: str,
    plan_sha: str,
    activation_nonce: UUID,
    db_now: datetime,
) -> None:
    """Require exactly four fresh, accepted, no-spend projections."""
    if tuple(capture.get("provider") for capture in captures) != PROVIDERS:
        raise EvidenceHoldError("exact_four_provider_captures_required")
    for capture in captures:
        provider = cast("str", capture["provider"])
        require_bindings(
            capture,
            reviewed_sha=reviewed_sha,
            plan_sha=plan_sha,
            activation_nonce=activation_nonce,
        )
        spend_enabled = (
            capture.get("plan") != PROVIDER_PLANS[provider]
            or capture.get("paid_enabled") is not False
            or capture.get("overage_enabled") is not False
            or capture.get("add_ons_enabled") is not False
            or capture.get("accepted") is not True
        )
        if spend_enabled:
            raise EvidenceHoldError("provider_spend_enabled")
        require_fresh(capture, db_now)


def _sha(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def require_graph_content(
    join: Mapping[str, object],
    captures: Sequence[Mapping[str, object]],
    production: Mapping[str, object],
    free_tier: Mapping[str, object],
) -> None:
    """Bind the joined capture/measurement branches to consumed bytes."""
    hashes = join.get("branch_input_sha256s")
    if not isinstance(hashes, dict):
        raise EvidenceHoldError("evidence_graph_hashes_missing")
    expected = {
        "github-capture": _sha(captures[0]),
        "vercel-api-capture": _sha(captures[1]),
        "vercel-web-capture": _sha(captures[2]),
        "supabase-capture": _sha(captures[3]),
        "production-measurement": _sha(production),
        "local-measurement": free_tier.get("measurements_sha256"),
        "quota-manifest": free_tier.get("manifest_sha256"),
    }
    if expected != hashes:
        raise EvidenceHoldError("evidence_graph_content_mismatch")


def require_free_tier(
    result: Mapping[str, object],
    *,
    evidence_join_receipt: Mapping[str, object],
    reviewed_sha: str,
    plan_sha: str,
    activation_nonce: UUID,
) -> None:
    """Require known complete dimensions strictly below 70 percent."""
    require_bindings(
        result,
        reviewed_sha=reviewed_sha,
        plan_sha=plan_sha,
        activation_nonce=activation_nonce,
    )
    if (
        result.get("accepted") is not True
        or result.get("phase") != "pre-0010"
        or result.get("predecessor_receipt_sha256")
        != receipt_sha256(evidence_join_receipt)
    ):
        raise EvidenceHoldError("free_tier_result_rejected")
    dimensions = result.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise EvidenceHoldError("free_tier_dimensions_missing")
    for item in cast("list[object]", dimensions):
        if not isinstance(item, dict):
            raise EvidenceHoldError("free_tier_dimension_rejected")
        dimension = cast("Mapping[str, object]", item)
        ratio = dimension.get("ratio")
        if (
            not isinstance(ratio, (int, float))
            or isinstance(ratio, bool)
            or float(ratio) >= THRESHOLD
            or dimension.get("accepted") is not True
            or dimension.get("status", "known") != "known"
        ):
            raise EvidenceHoldError("free_tier_dimension_rejected")


__all__ = (
    "evidence_time",
    "require_bindings",
    "require_free_tier",
    "require_fresh",
    "require_graph_content",
    "require_provider_captures",
)
