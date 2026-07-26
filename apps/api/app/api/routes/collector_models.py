"""Pydantic request and response contracts for collector routes."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this at runtime.
from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from app.collection.adapters.models import (  # noqa: TC001 - Pydantic runtime field.
    SourceAuthorizationDecision,
)
from app.domain.enums import (  # noqa: TC001 - Pydantic runtime fields.
    BudgetDecisionStatus,
    CommandStatus,
    RunStatus,
    SourcePlatform,
)

LeaseToken = Annotated[
    str,
    StringConstraints(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]
_INVALID_SKIP_OBSERVATION = "invalid_skip_observation"
_INVALID_SKIP_MESSAGE = "provider status and code disagree"


class _CollectorModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        from_attributes=True,
        validate_by_name=True,
    )


class MaterializePayload(_CollectorModel):
    """One scope and its deployment activation boundary."""

    scope_version: str = Field(min_length=1, max_length=80)
    deployment_activation_at: datetime


class MaterializeResponse(_CollectorModel):
    """All database-time due command identities."""

    command_ids: tuple[UUID, ...]


class ReservePayload(_CollectorModel):
    """One-use reservation and lease secrets."""

    reservation_nonce: LeaseToken
    lease_token: LeaseToken


class ConfirmPayload(_CollectorModel):
    """GitHub acceptance facts for one reserved attempt."""

    attempt: int = Field(ge=1, le=3)
    reservation_nonce: LeaseToken
    github_run_id: str = Field(min_length=1, max_length=100)
    github_run_attempt: int = Field(ge=1)


class ClaimPayload(_CollectorModel):
    """Exact attempt, proofs, and source set claimed by a workflow."""

    attempt: int = Field(ge=1, le=3)
    lease_token: LeaseToken
    reservation_nonce: LeaseToken
    source_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)


class HeartbeatPayload(_CollectorModel):
    """Attempt-bound lease proof for a heartbeat."""

    attempt: int = Field(ge=1, le=3)
    lease_token: LeaseToken


class CommandResponse(_CollectorModel):
    """Public durable command lifecycle facts."""

    command_id: UUID = Field(validation_alias="id")
    status: CommandStatus
    attempt: int
    available_at: datetime
    heartbeat_at: datetime | None


class ClaimedRunResponse(_CollectorModel):
    """Server-created run and persisted page progress."""

    run_id: UUID = Field(validation_alias="id")
    source_id: UUID
    scope_version: str
    attempt: int
    status: RunStatus
    start_checkpoint_revision: int
    start_cursor: str | None
    next_page_ordinal: int
    committed_page_count: int
    committed_page_hash_chain: str
    authorization_decision_id: UUID
    authorization: SourceAuthorizationDecision = Field(
        validation_alias="authorization_snapshot"
    )
    budget_decision_id: UUID
    budget_decision_status: BudgetDecisionStatus
    reviewed_page_cap: int = Field(ge=0)
    reviewed_post_cap: int = Field(ge=0)
    skip_decision_id: UUID | None
    precomputed_terminal_status: RunStatus | None


class ClaimResponse(_CollectorModel):
    """Claimed command paired with its exact source runs."""

    command: CommandResponse
    runs: tuple[ClaimedRunResponse, ...]


class CheckpointResponse(_CollectorModel):
    """Sole persisted cursor and page-chain replay position."""

    expected_checkpoint_revision: int
    expected_cursor: str | None
    next_page_ordinal: int
    committed_page_hash_chain: str
    accepted_count: int = 0
    last_page_commit_id: UUID | None = None


class SkipDecisionPayload(_CollectorModel):
    """Redacted provider observation bound to one active zero-commit run."""

    command_id: UUID
    attempt: int = Field(ge=1, le=3)
    lease_token: LeaseToken
    idempotency_key: UUID
    provider: SourcePlatform
    route: str = Field(min_length=1, max_length=300, pattern=r"^/[^?\r\n]*$")
    http_status: Literal[401, 403, 429]
    failure_code: Literal["provider_authorization_rejected", "provider_quota_exhausted"]

    @model_validator(mode="after")
    def require_status_code_pair(self) -> Self:
        """Reject client-selected reasons that disagree with the status allowlist."""
        policy = self.http_status in (401, 403)
        if policy != (self.failure_code == "provider_authorization_rejected"):
            raise PydanticCustomError(_INVALID_SKIP_OBSERVATION, _INVALID_SKIP_MESSAGE)
        return self


class SkipDecisionResponse(_CollectorModel):
    """Server-derived proof attached for completion finalization."""

    skip_decision_id: UUID
    terminal_status: Literal[RunStatus.SKIPPED_POLICY, RunStatus.SKIPPED_QUOTA]
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


__all__ = (
    "CheckpointResponse",
    "ClaimPayload",
    "ClaimResponse",
    "ClaimedRunResponse",
    "CommandResponse",
    "ConfirmPayload",
    "HeartbeatPayload",
    "LeaseToken",
    "MaterializePayload",
    "MaterializeResponse",
    "ReservePayload",
    "SkipDecisionPayload",
    "SkipDecisionResponse",
)
