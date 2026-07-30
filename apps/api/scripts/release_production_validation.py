"""Fail-closed validation of injected Production observations."""

# ruff: noqa: EM101, PLR2004

from __future__ import annotations

from datetime import UTC
from hashlib import sha256
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from .release_chain_common import Bindings, JsonObject, JsonValue, ReleaseChainError
from .release_production_data import validate_database_and_search

if TYPE_CHECKING:
    from datetime import datetime

    from .release_production_evidence import EvidenceDigests
    from .release_production_models import ProductionObservation, ProductionRequest

REVISION: Final = "20260727_0011"
PROJECTS: Final = {
    "api": "prediction-monitor-api",
    "web": "prediction-monitor-web",
}
HEX: Final = frozenset("0123456789abcdef")


def validate_request(request: ProductionRequest, bindings: Bindings) -> None:
    """Validate immutable caller bindings and neutral Production URLs."""
    if (
        request.expected_sha != bindings.reviewed_sha
        or request.expected_plan_sha256 != bindings.approved_plan_sha256
        or request.activation_nonce != bindings.activation_nonce
    ):
        raise ReleaseChainError("caller_binding_mismatch")
    if request.expected_revision != REVISION:
        raise ReleaseChainError("expected_revision_not_0011")
    if not request.database_url_env:
        raise ReleaseChainError("database_url_env_missing")
    for value in (request.api_url, request.web_url):
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not host
            or host in {"localhost", "127.0.0.1", "::1"}
            or any(part in host for part in ("fixture", "stub"))
        ):
            raise ReleaseChainError("production_url_invalid")


def validate_observation(
    request: ProductionRequest,
    observation: ProductionObservation,
    bindings: Bindings,
    evidence: EvidenceDigests,
    release_chain_sha256: str,
) -> JsonObject:
    """Require exact deployments, durable state, search, privacy, and freshness."""
    deployments = _deployments(observation, bindings)
    validate_database_and_search(
        observation,
        request,
        bindings,
        evidence,
        release_chain_sha256,
    )
    database, search = observation.database, observation.search
    return {
        "verification_mode": (
            "f3-read-only" if request.read_only else "todo12-read-only"
        ),
        "transaction_read_only": True,
        "expected_revision": REVISION,
        "release_chain_sha256": release_chain_sha256,
        "attestation_sha256": evidence.attestation_sha256,
        "attestation_generation": evidence.attestation_generation,
        "free_tier_sha256": evidence.free_tier_sha256,
        "api_url_sha256": sha256(request.api_url.encode()).hexdigest(),
        "web_url_sha256": sha256(request.web_url.encode()).hexdigest(),
        "deployments": deployments,
        "source": {
            "state": "active",
            "enabled": True,
            "binding_verified": True,
            "source_id_sha256": database.source_id_sha256,
            "cadence_anchor_at": _time(database.cadence_anchor_at),
            "authorization_expires_at": _time(database.authorization_expires_at),
        },
        "search": {
            "literal_sha256": search.literal_sha256,
            "negative_literal_sha256": search.negative_literal_sha256,
            "positive_total": search.positive_total,
            "negative_total": 0,
            "page": 1,
            "page_size": 50,
            "keyword_total": search.keyword_total,
            "and_total": search.and_total,
            "structured_identity_present": False,
            "provider_payload_retained": False,
        },
        "freshness": {
            "latest_manifold_at": _time(search.latest_manifold_at),
            "visible": True,
            "dcinside_90d_count": database.dcinside_90d_count,
        },
        "dcinside_unchanged": True,
        "two_source_30d": "PASS" if search.dcinside_recent else "HOLD",
        "cadence_30d": "PASS" if search.cadence_complete else "HOLD",
    }


def _deployments(
    observation: ProductionObservation,
    bindings: Bindings,
) -> list[JsonValue]:
    indexed = {item.kind: item for item in observation.deployments}
    if len(indexed) != 2 or set(indexed) != set(PROJECTS):
        raise ReleaseChainError("deployment_set_not_exact")
    result: list[JsonValue] = []
    for kind in ("api", "web"):
        item = indexed[kind]
        if (
            item.project_name != PROJECTS[kind]
            or not item.protected_identity_match
            or item.state != "READY"
            or not item.production
            or item.reviewed_sha != bindings.reviewed_sha
            or not item.health_ok
            or not item.health_database_backed
        ):
            raise ReleaseChainError("deployment_not_ready")
        hashes = (
            item.project_identity_sha256,
            item.deployment_identity_sha256,
            item.team_identity_sha256,
        )
        if not all(_hex(value) for value in hashes):
            raise ReleaseChainError("deployment_identity_invalid")
        result.append(
            {
                "kind": kind,
                "project_name": item.project_name,
                "project_identity_sha256": item.project_identity_sha256,
                "deployment_identity_sha256": item.deployment_identity_sha256,
                "team_identity_sha256": item.team_identity_sha256,
                "state": "READY",
                "production": True,
                "health": "DB_BACKED_OK",
            }
        )
    if (
        indexed["api"].project_identity_sha256
        == indexed["web"].project_identity_sha256
        or indexed["api"].deployment_identity_sha256
        == indexed["web"].deployment_identity_sha256
        or indexed["api"].team_identity_sha256
        != indexed["web"].team_identity_sha256
    ):
        raise ReleaseChainError("deployment_identity_collision")
    return result


def _hex(value: str) -> bool:
    return len(value) == 64 and set(value) <= HEX


def _aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ReleaseChainError("production_time_not_timezone_aware")


def _time(value: datetime) -> str:
    _aware(value)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ("REVISION", "validate_observation", "validate_request")
