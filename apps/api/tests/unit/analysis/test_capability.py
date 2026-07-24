from datetime import UTC, datetime, timedelta

from app.analysis.capability import (
    REQUIRED_CAPABILITIES,
    CapabilityApproved,
    CapabilityBlocked,
    CapabilityPolicy,
    CapabilityProof,
    CapabilityProofStatus,
    evaluate_capabilities,
)

NOW = datetime(2026, 7, 22, tzinfo=UTC)
CLI_VERSION = "codex-cli 0.144.1"
HARNESS_VERSION = "worker-harness-v1"


def _approved_proofs() -> tuple[CapabilityProof, ...]:
    return tuple(
        CapabilityProof(
            requirement=requirement,
            status=CapabilityProofStatus.APPROVED,
            codex_cli_version=CLI_VERSION,
            harness_version=HARNESS_VERSION,
            artifact_sha256=f"{ordinal:064x}",
            observed_at=NOW - timedelta(minutes=1),
            expires_at=NOW + timedelta(days=1),
        )
        for ordinal, requirement in enumerate(REQUIRED_CAPABILITIES, start=1)
    )


def test_capability_gate_blocks_when_any_required_proof_is_missing() -> None:
    # Given
    policy = CapabilityPolicy(
        codex_cli_version=CLI_VERSION,
        harness_version=HARNESS_VERSION,
        evaluated_at=NOW,
    )

    # When
    decision = evaluate_capabilities((), policy)

    # Then
    assert isinstance(decision, CapabilityBlocked)
    assert decision.status == "blocked_capability"
    assert tuple(reason.code for reason in decision.reasons) == (
        "pro_tier_unverified",
        "automation_terms_unverified",
        "zero_tools_unproven",
        "zero_network_boundary_unproven",
        "zero_filesystem_read_unproven",
        "low_privilege_token_unproven",
        "hard_resource_caps_unproven",
        "hostile_probe_blocked",
    )


def test_capability_gate_approves_only_complete_version_bound_proof_set() -> None:
    # Given
    policy = CapabilityPolicy(CLI_VERSION, HARNESS_VERSION, NOW)

    # When
    decision = evaluate_capabilities(_approved_proofs(), policy)

    # Then
    assert isinstance(decision, CapabilityApproved)
    assert decision.status == "approved"
    assert len(decision.proof_set_sha256) == 64


def test_capability_gate_blocks_a_stale_cli_version() -> None:
    # Given
    policy = CapabilityPolicy("codex-cli 0.145.0", HARNESS_VERSION, NOW)

    # When
    decision = evaluate_capabilities(_approved_proofs(), policy)

    # Then
    assert isinstance(decision, CapabilityBlocked)
    assert decision.reasons[0].code == "version_binding_mismatch"
