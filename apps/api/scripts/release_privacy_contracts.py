"""Closed contracts for the release privacy-incident boundary."""

# ruff: noqa: D102, TC003

from __future__ import annotations

from datetime import datetime
from typing import Annotated, ClassVar, Literal, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
CommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
ViolationKind = Literal["privacy", "authorization"]


class ClosedModel(BaseModel):
    """Forbid undeclared values at every incident boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class IncidentScope(ClosedModel):
    """Protected identifiers used only inside injected adapters."""

    source_id: UUID
    epoch_id: UUID
    activation_nonce: UUID
    violation_kind: ViolationKind
    predecessor_sha256: Sha256
    reviewed_sha: CommitSha
    approved_plan_sha256: Sha256


class ArtifactTarget(ClosedModel):
    """One protected Actions artifact."""

    kind: Literal["artifact"] = "artifact"
    artifact_id: int = Field(gt=0)


class WorkflowTarget(ClosedModel):
    """One protected Actions run and its frozen containment status."""

    kind: Literal["workflow"] = "workflow"
    run_id: int = Field(gt=0)
    status: Literal[
        "queued",
        "in_progress",
        "waiting",
        "pending",
        "requested",
        "completed",
    ]


class CacheTarget(ClosedModel):
    """One protected Actions cache key."""

    kind: Literal["cache"] = "cache"
    key: SecretStr


FrozenTarget = ArtifactTarget | WorkflowTarget | CacheTarget


class ContainmentMutation(ClosedModel):
    """Atomic database result returned after containment."""

    observed_at: datetime
    source_disabled: Literal[True]
    current_pointers_cleared: Literal[True]
    reads_blocked: Literal[True]
    state: Literal["deactivated"]
    frozen_targets: tuple[FrozenTarget, ...]
    mutation_sha256: Sha256


class DatabasePurgeMutation(ClosedModel):
    """Exact activation/epoch purge and zero-hash result."""

    observed_at: datetime
    affected_content_deleted: Literal[True]
    zero_title_body_url_hashes: Literal[True]
    dcinside_intact: Literal[True]
    deleted_row_count: int = Field(ge=0)
    mutation_sha256: Sha256


class DatabaseVerification(ClosedModel):
    """Current database, search, API, and DCInside observations."""

    database_content_zero: bool
    database_search_zero: bool
    dcinside_intact: bool
    source_disabled: bool
    current_pointers_cleared: bool
    revision: str
    latest_state: str
    verification_sha256: Sha256


class GitHubVerification(ClosedModel):
    """Absence proof for every frozen GitHub target."""

    artifacts_absent: bool
    caches_absent: bool
    logs_return_404: bool
    checked_target_count: int = Field(ge=0)
    verification_sha256: Sha256


class ProviderVerification(ClosedModel):
    """Static and access-restricted provider surface verification."""

    zero_provider_binding: bool
    direct_api_zero: bool
    aliases_and_health_restored: bool
    repository_static_scan_clean: bool
    public_surfaces_static_scan_clean: bool
    provider_logs_clean: bool
    provider_log_search_conclusive: bool
    provider_logs_deleted_or_expired: bool
    static_scan_sha256: Sha256
    provider_log_disposition_sha256: Sha256


class RestoreMutation(ClosedModel):
    """Terminal transition owned solely by privacy-verify."""

    observed_at: datetime
    prior_state: Literal["restore_writing"]
    state: Literal["restored"]
    mutation_sha256: Sha256


class GitHubCommand(ClosedModel):
    """Token-free exact argv passed to the injected GitHub adapter."""

    argv: tuple[str, ...]


class GitHubCommandResult(ClosedModel):
    """Redacted result of one GitHub mutation."""

    succeeded: bool
    status_sha256: Sha256


class PrivacyDatabase(Protocol):
    """Required database mutation and verification adapter."""

    async def contain(self, scope: IncidentScope) -> ContainmentMutation: ...

    async def frozen_targets(
        self,
        scope: IncidentScope,
    ) -> tuple[FrozenTarget, ...]: ...

    async def purge(
        self,
        scope: IncidentScope,
        containment_sha256: Sha256,
    ) -> DatabasePurgeMutation: ...

    async def verify(self, scope: IncidentScope) -> DatabaseVerification: ...

    async def append_restored(
        self,
        scope: IncidentScope,
        purge_sha256: Sha256,
        matrix_b_sha256: Sha256,
    ) -> RestoreMutation: ...


class PrivacyGitHub(Protocol):
    """Required public GitHub mutation and absence adapter."""

    async def execute(self, command: GitHubCommand) -> GitHubCommandResult: ...

    async def verify_absent(
        self,
        targets: tuple[FrozenTarget, ...],
    ) -> GitHubVerification: ...


class PrivacyProvider(Protocol):
    """Required provider-log, binding, deployment, and scan adapter."""

    async def verify(self, scope: IncidentScope) -> ProviderVerification: ...


__all__ = (
    "ArtifactTarget",
    "CacheTarget",
    "ContainmentMutation",
    "DatabasePurgeMutation",
    "DatabaseVerification",
    "FrozenTarget",
    "GitHubCommand",
    "GitHubCommandResult",
    "GitHubVerification",
    "IncidentScope",
    "PrivacyDatabase",
    "PrivacyGitHub",
    "PrivacyProvider",
    "ProviderVerification",
    "RestoreMutation",
    "Sha256",
    "ViolationKind",
    "WorkflowTarget",
)
