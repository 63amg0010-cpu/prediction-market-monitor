"""Typed completion boundary, locked facts, and mutation plans."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.domain.enums import CommandStatus, RunStatus
from app.services.configuration.canonical import canonical_sha256

from .checkpoint import CheckpointState, RunState
from .commands import CommandState
from .page_commit import PageCommitRecord

LeaseToken = Annotated[
    str,
    StringConstraints(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


@unique
class FailureClass(StrEnum):
    """Server-recognized source failure retry classes."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"


class FailureDetail(BaseModel):
    """Redacted source failure evidence supplied at completion."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    failure_class: FailureClass = Field(alias="class")
    code: str = Field(min_length=1, max_length=120)
    fingerprint: Sha256Hex
    observed_at: datetime
    retry_after_at: datetime | None


class CompletionSourceOutcome(BaseModel):
    """One asserted terminal outcome compared with persisted run facts."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    terminal_status: RunStatus
    last_page_commit_id: UUID | None
    final_cursor: str | None
    final_page_ordinal: int | None = Field(ge=0)
    committed_page_count: int = Field(ge=0)
    committed_page_hash_chain: Sha256Hex
    skip_decision_id: UUID | None
    failure: FailureDetail | None


class CompletionRequest(BaseModel):
    """Strict idempotent command-completion request boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    completion_idempotency_key: UUID
    attempt: int = Field(ge=1, le=3)
    lease_token: LeaseToken
    source_outcomes: tuple[CompletionSourceOutcome, ...] = Field(min_length=1)


class _CompletionIdentity(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    attempt: int
    source_outcomes: tuple[CompletionSourceOutcome, ...]


def completion_request_hash(request: CompletionRequest) -> str:
    """Hash source outcomes in stable run-identity order for replay checks."""
    return completion_outcomes_hash(request.attempt, request.source_outcomes)


def completion_outcomes_hash(
    attempt: int,
    source_outcomes: tuple[CompletionSourceOutcome, ...],
) -> str:
    """Hash server-derived outcomes with the same external completion identity."""
    ordered = tuple(sorted(source_outcomes, key=lambda item: item.run_id.hex))
    return canonical_sha256(
        _CompletionIdentity(attempt=attempt, source_outcomes=ordered)
    )


@dataclass(frozen=True, slots=True)
class ObservedPostVersion:
    """Persisted post-version identity observed by a run."""

    id: UUID
    content_hash: str


@dataclass(frozen=True, slots=True)
class SkipDecisionProof:
    """Current server-owned decision authorizing one skip status."""

    id: UUID
    terminal_status: RunStatus


@dataclass(frozen=True, slots=True)
class RunCompletionFacts:
    """Locked facts used to validate one run without client trust."""

    run: RunState
    checkpoint: CheckpointState
    commits: tuple[PageCommitRecord, ...]
    observed_post_versions: tuple[ObservedPostVersion, ...]
    skip_decision: SkipDecisionProof | None


@dataclass(frozen=True, slots=True)
class CompletionContext:
    """Locked command and run set for one atomic finalization."""

    command: CommandState
    runs: tuple[RunCompletionFacts, ...]
    db_now: datetime


@dataclass(frozen=True, slots=True)
class PublicationDraft:
    """Successful source publication awaiting sequence allocation."""

    run_id: UUID
    source_id: UUID
    terminal_page_commit_id: UUID
    final_chain_hash: str
    post_set_hash: str
    distinct_post_version_count: int
    zero_post: bool


@dataclass(frozen=True, slots=True)
class CompletionPlan:
    """Fully validated atomic run, command, and publication mutations."""

    request_hash: str
    command_status: CommandStatus
    runs: tuple[RunState, ...]
    publications: tuple[PublicationDraft, ...]
    completed_at: datetime


class CompletionPublicationResponse(BaseModel):
    """Persisted source-local publication returned by finalization."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    run_id: UUID
    source_id: UUID
    sequence: int = Field(gt=0)
    post_set_hash: Sha256Hex
    zero_post: bool


class CompletionResponse(BaseModel):
    """Byte-stable successful command finalization response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    command_id: UUID
    status: CommandStatus
    completed_at: datetime
    publications: tuple[CompletionPublicationResponse, ...]


@dataclass(frozen=True, slots=True)
class CompletionOutcome:
    """HTTP-neutral replayable completion result."""

    status_code: Literal[200]
    response: CompletionResponse
    response_bytes: bytes
