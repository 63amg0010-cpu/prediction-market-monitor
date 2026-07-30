"""Schema-closed cadence workflow recording contracts."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

if TYPE_CHECKING:
    import datetime as dt
    import uuid

    from app.services.identity.github import GitHubOIDCClaims

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ScheduleKind = Literal["collection", "verifier"]
WorkflowMode = Literal["schedule", "retry", "manual"]
SECOND_ATTEMPT = 2


class SourceResult(BaseModel):
    """Public-safe exact source result emitted by the legacy operation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    source_id: uuid.UUID
    succeeded: bool
    receipt_sha256: Sha256Hex


class CadenceWorkflowAttemptRequest(BaseModel):
    """Exact GitHub run and frozen cadence slot submitted after operation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    repository: str = Field(min_length=1, max_length=200)
    workflow: Literal["collect.yml", "verify.yml"]
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    ref: Literal["refs/heads/main"]
    event: Literal["schedule", "workflow_dispatch"]
    environment: Literal["production-collector", "production-verifier"]
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    epoch_id: uuid.UUID
    schedule_kind: ScheduleKind
    slot_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$")
    workflow_mode: WorkflowMode
    cadence_attempt: int = Field(ge=1, le=2)
    failed_predecessor_attempt_id: uuid.UUID | None = None
    started_at: dt.datetime
    completed_at: dt.datetime
    source_results: tuple[SourceResult, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def require_attempt_branch_shape(self) -> CadenceWorkflowAttemptRequest:
        """Reject structurally impossible initial/retry rows before SQL."""
        initial = (
            self.cadence_attempt == 1
            and self.failed_predecessor_attempt_id is None
        )
        retry = (
            self.cadence_attempt == SECOND_ATTEMPT
            and self.failed_predecessor_attempt_id is not None
        )
        if not (initial or retry):
            message = "cadence_attempt_branch_invalid"
            raise ValueError(message)
        return self


class CadenceWorkflowAttemptReceipt(BaseModel):
    """Schema-closed receipt; recording and cadence credit are distinct."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True
    )
    schema_version: Literal["cadence-workflow-attempt-receipt.v1"] = Field(
        alias="schema"
    )
    attempt_id: uuid.UUID
    recorded: Literal[True]
    cadence_accepted: bool
    reason: str
    retry_permitted: bool
    created_at_db: dt.datetime
    receipt_sha256: Sha256Hex


class CadenceWorkflowRecorder(Protocol):
    """Authenticated attempt recording boundary."""

    async def record(
        self, token: SecretStr, payload: CadenceWorkflowAttemptRequest
    ) -> CadenceWorkflowAttemptReceipt:
        """Record one authenticated attempt and return its stable receipt."""
        ...


class CadenceOidcAuthorizer(Protocol):
    """Exact GitHub workflow identity verification boundary."""

    async def authorize(
        self,
        token: SecretStr,
        payload: CadenceWorkflowAttemptRequest,
    ) -> GitHubOIDCClaims:
        """Verify one token against every request identity field."""
        ...


_RUNTIME_TYPES = {
    "dt": importlib.import_module("datetime"),
    "uuid": importlib.import_module("uuid"),
}
_ = SourceResult.model_rebuild(_types_namespace=_RUNTIME_TYPES)
_ = CadenceWorkflowAttemptRequest.model_rebuild(
    _types_namespace=_RUNTIME_TYPES
)
_ = CadenceWorkflowAttemptReceipt.model_rebuild(
    _types_namespace=_RUNTIME_TYPES
)


__all__ = (
    "SECOND_ATTEMPT",
    "CadenceOidcAuthorizer",
    "CadenceWorkflowAttemptReceipt",
    "CadenceWorkflowAttemptRequest",
    "CadenceWorkflowRecorder",
    "SourceResult",
)
