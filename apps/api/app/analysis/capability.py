"""Version-bound capability proof gate."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from hashlib import sha256
from typing import ClassVar, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field


@unique
class CapabilityRequirement(StrEnum):
    """Safety facts that must all have current direct evidence."""

    PRO_TIER = "pro_tier"
    AUTOMATION_TERMS = "automation_terms"
    ZERO_TOOLS = "zero_tools"
    ZERO_NETWORK = "zero_network"
    ZERO_FILESYSTEM_READ = "zero_filesystem_read"
    LOW_PRIVILEGE_PROCESS = "low_privilege_process"
    HARD_RESOURCE_CAPS = "hard_resource_caps"
    HOSTILE_PROBE = "hostile_probe"


REQUIRED_CAPABILITIES: tuple[CapabilityRequirement, ...] = tuple(CapabilityRequirement)

_FAILURE_CODES: dict[CapabilityRequirement, str] = {
    CapabilityRequirement.PRO_TIER: "pro_tier_unverified",
    CapabilityRequirement.AUTOMATION_TERMS: "automation_terms_unverified",
    CapabilityRequirement.ZERO_TOOLS: "zero_tools_unproven",
    CapabilityRequirement.ZERO_NETWORK: "zero_network_boundary_unproven",
    CapabilityRequirement.ZERO_FILESYSTEM_READ: "zero_filesystem_read_unproven",
    CapabilityRequirement.LOW_PRIVILEGE_PROCESS: "low_privilege_token_unproven",
    CapabilityRequirement.HARD_RESOURCE_CAPS: "hard_resource_caps_unproven",
    CapabilityRequirement.HOSTILE_PROBE: "hostile_probe_blocked",
}


@unique
class CapabilityProofStatus(StrEnum):
    """Persisted proof lifecycle used by the AND gate."""

    APPROVED = "approved"
    FAILED = "failed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class CapabilityProof(BaseModel):
    """One immutable, version-bound proof record."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )

    requirement: CapabilityRequirement
    status: CapabilityProofStatus
    codex_cli_version: str = Field(min_length=1, max_length=100)
    harness_version: str = Field(min_length=1, max_length=100)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """Expected runtime versions and database evaluation time."""

    codex_cli_version: str
    harness_version: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class CapabilityBlockReason:
    """Stable machine-readable reason preventing worker activation."""

    code: str
    requirement: CapabilityRequirement | None = None


@dataclass(frozen=True, slots=True)
class CapabilityApproved:
    """Unforgeable-by-convention permit returned only by the complete gate."""

    proof_set_sha256: str
    status: Literal["approved"] = "approved"


@dataclass(frozen=True, slots=True)
class CapabilityBlocked:
    """Fail-closed decision retaining every unsatisfied reason."""

    reasons: tuple[CapabilityBlockReason, ...]
    status: Literal["blocked_capability"] = "blocked_capability"


type CapabilityDecision = CapabilityApproved | CapabilityBlocked


def evaluate_capabilities(
    proofs: tuple[CapabilityProof, ...], policy: CapabilityPolicy
) -> CapabilityDecision:
    """Approve only one current exact-version proof per required capability."""
    by_requirement: dict[CapabilityRequirement, list[CapabilityProof]] = {
        requirement: [] for requirement in REQUIRED_CAPABILITIES
    }
    for proof in proofs:
        by_requirement[proof.requirement].append(proof)

    accepted: list[CapabilityProof] = []
    reasons: list[CapabilityBlockReason] = []
    for requirement in REQUIRED_CAPABILITIES:
        candidates = by_requirement[requirement]
        if len(candidates) != 1:
            reasons.append(
                CapabilityBlockReason(_FAILURE_CODES[requirement], requirement)
            )
            continue
        proof = candidates[0]
        match proof.status:  # noqa: RUF100  # noqa: MATCH_OK
            case CapabilityProofStatus.APPROVED:
                if (
                    proof.codex_cli_version != policy.codex_cli_version
                    or proof.harness_version != policy.harness_version
                ):
                    reasons.append(
                        CapabilityBlockReason("version_binding_mismatch", requirement)
                    )
                elif (
                    proof.observed_at > policy.evaluated_at
                    or proof.expires_at <= policy.evaluated_at
                ):
                    reasons.append(
                        CapabilityBlockReason(_FAILURE_CODES[requirement], requirement)
                    )
                else:
                    accepted.append(proof)
                continue
            case (
                CapabilityProofStatus.FAILED
                | CapabilityProofStatus.REVOKED
                | CapabilityProofStatus.EXPIRED
            ):
                reasons.append(
                    CapabilityBlockReason(_FAILURE_CODES[requirement], requirement)
                )
                continue
        assert_never(proof.status)

    if reasons:
        return CapabilityBlocked(tuple(reasons))
    digest_input = "\n".join(
        f"{proof.requirement.value}:{proof.artifact_sha256}" for proof in accepted
    ).encode()
    proof_set_sha256 = sha256(b"codex-capability-set/v1\n" + digest_input).hexdigest()
    return CapabilityApproved(proof_set_sha256)
