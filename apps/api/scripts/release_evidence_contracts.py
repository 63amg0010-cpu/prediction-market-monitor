"""Closed contracts for Todo 11 bootstrap and activation evidence."""

# ruff: noqa: D102, EM101, PLR2004

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from scripts.activation_evidence_models import PublicActivationAttestation

Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

EVIDENCE_KINDS = (
    "local-measurement",
    "quota-manifest",
    "github-capture",
    "vercel-api-capture",
    "vercel-web-capture",
    "supabase-capture",
    "production-measurement",
)
PRE_0010_KINDS = EVIDENCE_KINDS
PROVIDERS = ("github", "vercel-api", "vercel-web", "supabase")
PROVIDER_PLANS = {
    "github": "public-standard",
    "vercel-api": "hobby",
    "vercel-web": "hobby",
    "supabase": "free",
}


class EvidenceHoldError(RuntimeError):
    """Stable fail-closed evidence boundary error."""


class ClosedModel(BaseModel):
    """Immutable schema that rejects public or protected field smuggling."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ReviewLane(ClosedModel):
    """One descriptor-bound unconditional approval."""

    status: Literal["approved"]
    model: str = Field(min_length=1, max_length=100)
    reasoning_effort: str = Field(min_length=1, max_length=40)
    workspace_root: str = Field(min_length=1)
    runtime_home: str | None
    target: str = Field(min_length=1)
    round_id: str = Field(min_length=1)
    plan_sha256: Sha256
    plan_bytes: int = Field(gt=0)
    descriptor_chain_verified: Literal[True]
    regular_file: Literal[True]
    launch_id: str = Field(min_length=1)
    session: str = Field(min_length=1)
    result: Literal["OKAY"]


class ReviewPair(ClosedModel):
    """The two independently launched approval lanes."""

    momus: ReviewLane
    independent: ReviewLane


class ProtectedReviewRecord(ClosedModel):
    """Exact YAML front matter from the owner-only planning handoff."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    slug: str = Field(min_length=1)
    status: Literal["approved"]
    intent: str
    review_required: Literal[True]
    plan_path: str = Field(min_length=1)
    plan_sha256: Sha256
    plan_bytes: int = Field(gt=0)
    review_round_id: str = Field(min_length=1)
    round_status: Literal["approved"]
    pending_action: str = Field(alias="pending-action", min_length=1)
    review: ReviewPair
    approach: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class ReviewRecordAccess:
    """Injected filesystem/git facts; no handler probes external state."""

    committed: bool
    symlinked: bool
    world_readable: bool


@dataclass(frozen=True, slots=True)
class ReviewBindings:
    """Public-safe hashes derived only from a verified protected record."""

    reviewed_sha: str
    approved_plan_sha256: str
    approval_round_id: str
    approval_launch_sha256s: tuple[str, str]


class RedactedRatio(ClosedModel):
    """Public numeric projection without account or project identity."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    numerator: int = Field(ge=0)
    denominator: int = Field(gt=0)
    ratio: float = Field(ge=0, lt=0.70)

    @model_validator(mode="after")
    def require_truthful_ratio(self) -> RedactedRatio:
        if abs(self.ratio - (self.numerator / self.denominator)) > 1e-12:
            raise ValueError("redacted_ratio_mismatch")
        return self


@dataclass(frozen=True, slots=True)
class AttestationArtifact:
    """Canonical public bytes plus the private-chain-safe receipt."""

    attestation: PublicActivationAttestation
    canonical_attestation: bytes
    attestation_sha256: str
    receipt: dict[str, object]


class SecretRunner(Protocol):
    """Injected exact child runner whose return value contains no output."""

    def run(self, argv: tuple[str, ...], stdin: bytes) -> int: ...


__all__ = (
    "EVIDENCE_KINDS",
    "PRE_0010_KINDS",
    "PROVIDERS",
    "PROVIDER_PLANS",
    "AttestationArtifact",
    "EvidenceHoldError",
    "ProtectedReviewRecord",
    "PublicActivationAttestation",
    "RedactedRatio",
    "ReviewBindings",
    "ReviewRecordAccess",
    "SecretRunner",
)
