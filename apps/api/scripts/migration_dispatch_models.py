"""Closed public-safe models accepted by the migration dispatch boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal
from uuid import UUID  # noqa: TC003 - Pydantic resolves UUID at runtime.

from pydantic import BaseModel, ConfigDict


class ClosedModel(BaseModel):
    """Forbid undeclared workflow input fields and freeze parsed values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ProtectedIdentityHashes(ClosedModel):
    """One-way hashes for the exact protected deployment identities."""

    github_repository: str
    supabase_project: str
    vercel_api_project: str
    vercel_web_project: str


class ReviewRoot(ClosedModel):
    """Credential-free reviewed deployment root transported for bootstrap."""

    schema_version: Literal[1]
    command: Literal["deployment-prestate"]
    reviewed_sha: str
    approved_plan_sha256: str
    approval_round_id: str
    approval_launch_sha256s: tuple[str, str]
    activation_nonce: UUID
    public_provider_names: tuple[Literal["github", "supabase", "vercel"], ...]
    protected_identity_hashes: ProtectedIdentityHashes


class NoSpendReceipt(ClosedModel):
    """Credential-free complete no-spend decision transported for bootstrap."""

    schema_version: Literal[1]
    command: Literal["no-spend-preflight"]
    reviewed_sha: str
    approved_plan_sha256: str
    activation_nonce: UUID
    predecessor_receipt_sha256: str
    billing_disabled: Literal[True]
    projection_below_70_percent: Literal[True]


class FailedAttemptReceipt(ClosedModel):
    """DB-safe terminal proof that alone may authorize bootstrap attempt two."""

    schema_version: Literal[1]
    command: Literal["migrate-0010-bootstrap"]
    attempt: Literal[1]
    reviewed_sha: str
    approved_plan_sha256: str
    activation_nonce: UUID
    dispatch_nonce: UUID
    review_root_sha256: str
    no_spend_receipt_sha256: str
    run_id: int
    artifact_sha256: str
    accepted: Literal[False]
    terminal_for_attempt: Literal[True]
    retry_permitted: Literal[True]
    state_before: Literal["20260726_0009"]
    state_after: Literal["20260726_0009"]
    ledger_exists: Literal[False]
    manifold_data_exists: Literal[False]
    enum_residue: bool


class DispatchRequest(ClosedModel):
    """Untrusted GitHub workflow-dispatch input envelope."""

    operation: str
    revision: str
    attempt: int
    expected_commit_sha: str
    confirm: str
    activation_nonce: UUID
    dispatch_nonce: UUID
    expected_plan_sha256: str
    review_root_sha256: str
    review_root_b64: str
    no_spend_receipt_sha256: str
    no_spend_receipt_b64: str
    attempt1_failed_receipt_sha256: str
    attempt1_failed_receipt_b64: str
    attestation_run_id: str
    attestation_generation: int = 0
    attestation_dispatch_nonce: str = ""
    attestation_sha256: str
    reservation_sha256: str


class RunCandidate(ClosedModel):
    """Public GitHub run metadata used for exact dispatch correlation."""

    database_id: int
    workflow_path: str
    display_title: str
    head_sha: str
    event: str
    attempt: int
    dispatch_nonce: UUID


@dataclass(frozen=True, slots=True)
class ValidatedDispatch:
    """Inert exact mutation command derived from a fully validated request."""

    operation: Literal["upgrade", "downgrade"]
    revision: Literal["20260727_0010", "20260803_0010a", "20260727_0011"]
    attempt: Literal[1, 2]
    display_title: str
    alembic_argv: tuple[str, str, str, str, str]
