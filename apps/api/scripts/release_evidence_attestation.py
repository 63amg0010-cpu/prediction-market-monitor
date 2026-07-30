"""Public attestation creation and no-log Environment secret upload."""

# ruff: noqa: EM101, PLR0913, S105, TC003

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError

from scripts.release_evidence_contracts import (
    PROVIDER_PLANS,
    PROVIDERS,
    AttestationArtifact,
    EvidenceHoldError,
    PublicActivationAttestation,
    RedactedRatio,
    SecretRunner,
)
from scripts.release_evidence_graph import canonical_bytes, receipt_sha256
from scripts.release_evidence_preflight_checks import require_bindings

REPOSITORY = "63amg0010-cpu/prediction-market-monitor"
SECRET_NAME = "MANIFOLD_ACTIVATION_ATTESTATION_JSON"
ENVIRONMENT = "production-collector"
PUBLIC_HOST_SUFFIXES = (
    "github.com",
    "githubusercontent.com",
    "vercel.com",
    "supabase.com",
    "manifold.markets",
)


def _require_exact_captures(captures: Sequence[Mapping[str, object]]) -> None:
    if tuple(capture.get("provider") for capture in captures) != PROVIDERS:
        raise EvidenceHoldError("exact_four_provider_captures_required")
    for capture in captures:
        provider = cast("str", capture["provider"])
        if (
            capture.get("accepted") is not True
            or capture.get("plan") != PROVIDER_PLANS[provider]
            or capture.get("paid_enabled") is not False
            or capture.get("overage_enabled") is not False
            or capture.get("add_ons_enabled") is not False
        ):
            raise EvidenceHoldError("provider_capture_rejected")


def _ratios(free_tier: Mapping[str, object]) -> tuple[RedactedRatio, ...]:
    dimensions = free_tier.get("dimensions")
    if free_tier.get("accepted") is not True or not isinstance(dimensions, list):
        raise EvidenceHoldError("free_tier_result_rejected")
    ratios: list[RedactedRatio] = []
    try:
        for item in cast("list[object]", dimensions):
            if not isinstance(item, dict):
                raise EvidenceHoldError("free_tier_dimension_rejected")
            value = cast("Mapping[str, object]", item)
            payload = dict(value)
            quota = payload.pop("quota", None)
            _ = payload.setdefault("denominator", quota)
            ratios.append(RedactedRatio.model_validate(payload))
    except (KeyError, TypeError, ValueError, ValidationError) as error:
        raise EvidenceHoldError("free_tier_dimension_rejected") from error
    if not ratios:
        raise EvidenceHoldError("free_tier_dimensions_missing")
    return tuple(ratios)


def _require_neutral_urls(urls: Sequence[str]) -> None:
    for url in urls:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        neutral = (
            parsed.scheme == "https"
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and any(
                hostname == suffix or hostname.endswith(f".{suffix}")
                for suffix in PUBLIC_HOST_SUFFIXES
            )
        )
        if not neutral:
            raise EvidenceHoldError("public_evidence_url_not_neutral")


def _base_receipt(
    predecessor: Mapping[str, object],
    *,
    command: str,
    reviewed_sha: str,
    plan_sha: str,
    activation_nonce: UUID,
) -> dict[str, object]:
    if (
        predecessor.get("accepted") is not True
        or predecessor.get("reviewed_sha") != reviewed_sha
        or predecessor.get("approved_plan_sha256") != plan_sha
        or predecessor.get("activation_nonce") != str(activation_nonce)
    ):
        raise EvidenceHoldError("attestation_predecessor_rejected")
    return {
        "schema_version": 1,
        "command": command,
        "attempt": 1,
        "reviewed_sha": reviewed_sha,
        "approved_plan_sha256": plan_sha,
        "approval_round_id": predecessor.get("approval_round_id"),
        "approval_launch_sha256s": predecessor.get("approval_launch_sha256s"),
        "activation_nonce": str(activation_nonce),
        "dispatch_nonce": None,
        "state_before": predecessor.get("state_after"),
        "state_after": predecessor.get("state_after"),
        "accepted": True,
        "terminal_for_attempt": True,
        "retry_permitted": False,
        "predecessor_receipt_sha256": receipt_sha256(predecessor),
    }


def attest(
    *,
    provider_captures: Sequence[Mapping[str, object]],
    authorization_live_proof: Mapping[str, object],
    free_tier_result: Mapping[str, object],
    measurement_receipt: Mapping[str, object],
    attestation_generation: int,
    database_time: datetime,
    public_evidence_urls: Sequence[str],
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: UUID,
    predecessor_receipt: Mapping[str, object],
) -> AttestationArtifact:
    """Hash protected evidence into one schema-closed public attestation."""
    _require_exact_captures(provider_captures)
    others = (authorization_live_proof, free_tier_result, measurement_receipt)
    for evidence in (*provider_captures, *others):
        require_bindings(
            evidence,
            reviewed_sha=expected_sha,
            plan_sha=expected_plan_sha256,
            activation_nonce=activation_nonce,
        )
    if authorization_live_proof.get("accepted") is not True:
        raise EvidenceHoldError("authorization_live_proof_rejected")
    if (
        measurement_receipt.get("accepted") is not True
        or measurement_receipt.get("transaction_read_only") is not True
    ):
        raise EvidenceHoldError("measurement_receipt_rejected")
    _require_neutral_urls(public_evidence_urls)
    predecessor_sha = receipt_sha256(predecessor_receipt)
    capture_hashes = [receipt_sha256(capture) for capture in provider_captures]
    provenance = {
        "provider_capture_sha256s": capture_hashes,
        "measurement_receipt_sha256": receipt_sha256(measurement_receipt),
    }
    attestation = PublicActivationAttestation.model_validate(
        {
            "reviewed_sha": expected_sha,
            "activation_nonce": activation_nonce,
            "attestation_generation": attestation_generation,
            "database_time": database_time,
            "authorization_evidence_sha256": receipt_sha256(authorization_live_proof),
            "free_tier_evidence_sha256": receipt_sha256(free_tier_result),
            "provenance_sha256": receipt_sha256(provenance),
            "predecessor_receipt_sha256": predecessor_sha,
            "redacted_ratios": _ratios(free_tier_result),
            "public_evidence_urls": tuple(public_evidence_urls),
        }
    )
    public_bytes = canonical_bytes(attestation.model_dump(mode="json"))
    public_sha = hashlib.sha256(public_bytes).hexdigest()
    receipt = {
        **_base_receipt(
            predecessor_receipt,
            command="attest",
            reviewed_sha=expected_sha,
            plan_sha=expected_plan_sha256,
            activation_nonce=activation_nonce,
        ),
        "attestation_generation": attestation_generation,
        "attestation_sha256": public_sha,
        "authorization_evidence_sha256": attestation.authorization_evidence_sha256,
        "free_tier_evidence_sha256": attestation.free_tier_evidence_sha256,
        "provenance_sha256": attestation.provenance_sha256,
    }
    return AttestationArtifact(attestation, public_bytes, public_sha, receipt)


def attestation_secret_upload(
    runner: SecretRunner,
    *,
    canonical_attestation: bytes,
    predecessor_receipt: Mapping[str, object],
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: UUID,
) -> dict[str, object]:
    """Upload only an Environment secret with canonical bytes on child stdin."""
    try:
        parsed = PublicActivationAttestation.model_validate_json(canonical_attestation)
    except ValidationError as error:
        raise EvidenceHoldError("public_attestation_rejected") from error
    canonical = canonical_bytes(parsed.model_dump(mode="json"))
    digest = hashlib.sha256(canonical).hexdigest()
    if (
        not hmac.compare_digest(canonical, canonical_attestation)
        or predecessor_receipt.get("command") != "attest"
        or predecessor_receipt.get("attestation_sha256") != digest
    ):
        raise EvidenceHoldError("attestation_upload_predecessor_rejected")
    receipt = _base_receipt(
        predecessor_receipt,
        command="attestation-secret-upload",
        reviewed_sha=expected_sha,
        plan_sha=expected_plan_sha256,
        activation_nonce=activation_nonce,
    )
    argv = (
        "gh",
        "secret",
        "set",
        SECRET_NAME,
        "--repo",
        REPOSITORY,
        "--env",
        ENVIRONMENT,
    )
    if runner.run(argv, canonical) != 0:
        raise EvidenceHoldError("attestation_secret_upload_failed")
    return {
        **receipt,
        "attestation_generation": parsed.attestation_generation,
        "attestation_sha256": digest,
        "target": "github-environment-secret",
    }


__all__ = ("attest", "attestation_secret_upload")
