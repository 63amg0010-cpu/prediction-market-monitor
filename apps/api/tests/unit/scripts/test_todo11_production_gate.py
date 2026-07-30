# ruff: noqa: FBT003

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

import pytest
from app.services.release.receipts import canonicalize

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from apps.api.scripts.free_tier_domain import (
    JsonObject as EvidenceObject,
)
from apps.api.scripts.free_tier_domain import canonical_bytes
from apps.api.scripts.release_chain_common import (
    JsonObject,
    PathReceiptIO,
    ReleaseChainError,
)
from apps.api.scripts.release_production import handle_production
from apps.api.scripts.release_production_models import (
    DatabaseProof,
    DeploymentProof,
    ProductionObservation,
    ProductionProbeQuery,
    ProductionRequest,
    SearchProof,
)

NOW = datetime(2026, 7, 29, 3, tzinfo=UTC)
SHA, PLAN = "a" * 40, "b" * 64
NONCE = "11111111-1111-4111-8111-111111111111"
REVISION = "20260727_0011"
IO = PathReceiptIO()


def _receipt(command: str, predecessor: JsonObject | None) -> JsonObject:
    body: JsonObject = {
        "schema": "release-chain-receipt.v1",
        "command": command,
        "reviewed_sha": SHA,
        "approved_plan_sha256": PLAN,
        "approval_round_id": "c" * 64,
        "approval_launch_sha256s": ["d" * 64, "e" * 64],
        "activation_nonce": NONCE,
        "dispatch_nonce": None,
        "attempt": 0,
        "database_timestamps": {"created_at_db": "2026-07-29T03:00:00Z"},
        "accepted": True,
        "terminal_for_attempt": True,
        "retry_permitted": False,
        "predecessor_receipt_sha256": (
            predecessor["receipt_sha256"] if predecessor else None
        ),
    }
    if command == "materialize-chain":
        body["details"] = {
            "manifest_sha256": "f" * 64,
            "node_count": 2,
            "nodes": [],
            "terminal_command": "cadence-initial",
        }
    return {**body, "receipt_sha256": sha256(canonicalize(body)).hexdigest()}


def _write(path: Path, value: object) -> bytes:
    raw = canonical_bytes(cast("EvidenceObject", value))
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(raw)
    return raw


def _setup(tmp_path: Path) -> tuple[ProductionRequest, ProductionObservation]:
    chain = _receipt("materialize-chain", None)
    chain_raw = _write(tmp_path / "release-chain.json", chain)
    free_body: EvidenceObject = {
        "schema": "free-tier.result.v1",
        "accepted": True,
        "phase": "pre-0010",
        "reviewed_sha": SHA,
        "expected_plan_sha256": PLAN,
        "activation_nonce": NONCE,
        "db_now": "2026-07-29T03:00:00Z",
        "dimensions": [
            {"name": "database", "numerator": 10, "denominator": 100, "ratio": 0.1}
        ],
    }
    free_body["receipt_sha256"] = sha256(canonical_bytes(free_body)).hexdigest()
    free_raw = _write(tmp_path / "free-tier.json", free_body)
    attestation: EvidenceObject = {
        "schema_version": 1,
        "command": "activation-attestation",
        "reviewed_sha": SHA,
        "activation_nonce": NONCE,
        "attestation_generation": 1,
        "database_time": "2026-07-29T02:50:00Z",
        "authorization_evidence_sha256": "1" * 64,
        "free_tier_evidence_sha256": sha256(free_raw).hexdigest(),
        "provenance_sha256": "2" * 64,
        "predecessor_receipt_sha256": "3" * 64,
        "redacted_ratios": [
            {"name": "database", "numerator": 10, "denominator": 100, "ratio": 0.1}
        ],
        "public_evidence_urls": ["https://github.com"],
    }
    attestation_raw = _write(tmp_path / "attestation.json", attestation)
    request = ProductionRequest(
        "MIGRATION_DATABASE_URL", "https://api.example.com",
        "https://web.example.com", SHA, PLAN, NONCE,
        tmp_path / "release-chain.json", REVISION,
        tmp_path / "attestation.json", tmp_path / "free-tier.json",
        tmp_path / "release-chain.json", tmp_path / "production.json",
    )
    deployments = tuple(
        DeploymentProof(
            cast("Literal['api', 'web']", kind), f"prediction-monitor-{kind}",
            character * 64, character.upper() * 64, "9" * 64, "READY",
            True, SHA, True, True, True,
        )
        for kind, character in (("api", "4"), ("web", "5"))
    )
    database = DatabaseProof(
        REVISION, True, 0, SHA, PLAN, NONCE, sha256(chain_raw).hexdigest(),
        sha256(attestation_raw).hexdigest(), sha256(free_raw).hexdigest(),
        "active", True, True, "6" * 64, NOW - timedelta(hours=3),
        NOW - timedelta(hours=3) + timedelta(days=31),
        "7" * 64, "7" * 64, True, 0,
    )
    search = SearchProof(
        "production", True, False, False, False, True, "8" * 64, "0" * 64,
        3, 3, True, 0, 1, 50, True, True, True, 2, 1, True, True,
        False, False, True, NOW - timedelta(hours=1), NOW, False, False,
    )
    return request, ProductionObservation(deployments, database, search)


class _Probe:
    def __init__(self, observation: ProductionObservation) -> None:
        self.observation: ProductionObservation = observation
        self.queries: list[ProductionProbeQuery] = []

    def observe(self, query: ProductionProbeQuery) -> ProductionObservation:
        self.queries.append(query)
        return self.observation


def test_happy_path_is_injected_read_only_canonical_and_redacted(
    tmp_path: Path,
) -> None:
    request, observation = _setup(tmp_path)
    probe = _Probe(observation)
    result = handle_production(request, io=IO, clock=lambda: NOW, probe=probe)
    assert len(probe.queries) == 1
    assert probe.queries[0].read_only is True
    assert result["command"] == "production"
    details = cast("JsonObject", result["details"])
    assert details["cadence_30d"] == "HOLD"
    assert details["two_source_30d"] == "HOLD"
    raw = request.json_out.read_bytes()
    assert raw == canonicalize(result)
    assert b"example.com" not in raw


@pytest.mark.parametrize(
    ("section", "changes", "error"),
    [
        ("database", {"revision": "20260727_0010"}, "database_revision_mismatch"),
        ("database", {"transaction_read_only": False}, "production_probe_writable"),
        ("database", {"source_state": "prepared"}, "manifold_not_active"),
        ("database", {"dcinside_current_sha256": "0" * 64}, "dcinside_changed"),
        ("search", {"stub_evidence": True}, "nonproduction_evidence"),
        ("search", {"fixture_evidence": True}, "nonproduction_evidence"),
        ("search", {"positive_total": 0}, "literal_positive_missing"),
        ("search", {"negative_total": 1}, "literal_negative_matched"),
        ("search", {"page_size": 49}, "pagination_contract_failed"),
        ("search", {"and_total": 0}, "keyword_and_contract_failed"),
        ("search", {"structured_identity_present": True}, "identity_data_present"),
        ("search", {"freshness_visible": False}, "freshness_contract_failed"),
    ],
)
def test_wrong_database_or_data_proof_is_rejected_before_output(
    tmp_path: Path, section: str, changes: dict[str, object], error: str
) -> None:
    request, observation = _setup(tmp_path)
    changed = replace(getattr(observation, section), **changes)  # pyright: ignore[reportAny]
    proof = replace(observation, **{section: changed})
    with pytest.raises(ReleaseChainError, match=error):
        _ = handle_production(request, io=IO, clock=lambda: NOW, probe=_Probe(proof))
    assert not request.json_out.exists()


def test_wrong_deployment_and_missing_receipt_fail_closed(tmp_path: Path) -> None:
    request, observation = _setup(tmp_path)
    broken = replace(observation.deployments[0], state="ERROR")
    with pytest.raises(ReleaseChainError, match="deployment_not_ready"):
        _ = handle_production(
            request,
            io=IO,
            clock=lambda: NOW,
            probe=_Probe(
                replace(
                    observation,
                    deployments=(broken, observation.deployments[1]),
                )
            ),
        )
    request.attestation.unlink()
    probe = _Probe(observation)
    with pytest.raises(ReleaseChainError, match="evidence_missing"):
        _ = handle_production(request, io=IO, clock=lambda: NOW, probe=probe)
    assert probe.queries == []


def test_f3_requires_production_predecessor_and_stays_read_only(
    tmp_path: Path,
) -> None:
    request, observation = _setup(tmp_path)
    first = handle_production(
        request, io=IO, clock=lambda: NOW, probe=_Probe(observation)
    )
    f3 = replace(
        request,
        read_only=True,
        predecessor_receipt=request.json_out,
        json_out=tmp_path / "f3.json",
    )
    result = handle_production(f3, io=IO, clock=lambda: NOW, probe=_Probe(observation))
    assert result["predecessor_receipt_sha256"] == first["receipt_sha256"]
    invalid = replace(request, read_only=True, json_out=tmp_path / "invalid.json")
    with pytest.raises(ReleaseChainError, match="f3_predecessor_not_production"):
        _ = handle_production(
            invalid, io=IO, clock=lambda: NOW, probe=_Probe(observation)
        )
