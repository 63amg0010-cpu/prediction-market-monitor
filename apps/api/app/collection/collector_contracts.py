"""Typed collector workflow values and scoped control-plane port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from uuid import UUID

    from app.api.routes.collector import (
        CheckpointResponse,
        ClaimResponse,
        CommandResponse,
    )
    from app.api.routes.collector_models import (
        SkipDecisionPayload,
        SkipDecisionResponse,
    )
    from app.domain.enums import SourcePlatform

    from .adapters.models import (
        AdapterPage,
        PreflightResult,
        SourceAuthorizationDecision,
    )
    from .completion_models import CompletionRequest, CompletionResponse
    from .page_commit import PageCommitRequest, PageCommitResponse


class CollectorWorkflowError(RuntimeError):
    """Fail-closed orchestration error without provider bodies or credentials."""


@dataclass(frozen=True, slots=True)
class PageCursor:
    """Run progress advanced exclusively from persisted page receipts."""

    revision: int
    cursor: str | None
    ordinal: int
    accepted_count: int = 0
    committed_page_count: int = 0
    committed_page_hash_chain: str = ""
    last_page_commit_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class SourceExecution:
    """One DB source identity bound to an authorized adapter invocation."""

    source_id: UUID
    platform: SourcePlatform
    preflight: Callable[[], PreflightResult]
    fetch_page: Callable[[PageCursor], Awaitable[AdapterPage]]
    authorization: SourceAuthorizationDecision | None = None


@dataclass(frozen=True, slots=True)
class CommandSecrets:
    """Ephemeral reservation, lease, and completion identities for one command."""

    reservation_nonce: str
    lease_token: str
    completion_idempotency_key: UUID


@dataclass(frozen=True, slots=True)
class CollectionInvocation:
    """Validated GitHub run metadata and exact collection source set."""

    scope_version: str
    deployment_activation_at: datetime
    source_ids: tuple[UUID, ...]
    github_run_id: str
    github_run_attempt: int
    command_id: UUID | None = None


class CollectorControlPlane(Protocol):
    """Scoped HTTP operations used by the collector workflow."""

    async def authenticate(self) -> None:
        """Acquire a short-lived scoped API identity."""
        ...

    async def materialize(
        self, scope_version: str, deployment_activation_at: datetime
    ) -> tuple[UUID, ...]:
        """Return all DB-time due command identities."""
        ...

    async def reserve(
        self, command_id: UUID, reservation_nonce: str, lease_token: str
    ) -> CommandResponse:
        """Reserve a command with ephemeral claim material."""
        ...

    async def confirm(
        self,
        command_id: UUID,
        attempt: int,
        reservation_nonce: str,
        github_run_id: str,
        github_run_attempt: int,
    ) -> CommandResponse:
        """Bind a reservation to the current GitHub run."""
        ...

    async def claim(
        self,
        command_id: UUID,
        attempt: int,
        lease_token: str,
        reservation_nonce: str,
        source_ids: tuple[UUID, ...],
    ) -> ClaimResponse:
        """Claim the exact authorized source set."""
        ...

    async def checkpoint(self, run_id: UUID) -> CheckpointResponse:
        """Read the persisted run cursor."""
        ...

    async def commit_page(
        self, run_id: UUID, request: PageCommitRequest
    ) -> PageCommitResponse:
        """Commit one page and return its durable receipt."""
        ...

    async def heartbeat(
        self, command_id: UUID, attempt: int, lease_token: str
    ) -> CommandResponse:
        """Refresh the current command lease."""
        ...

    async def complete(
        self, command_id: UUID, request: CompletionRequest
    ) -> CompletionResponse:
        """Finalize all claimed runs atomically."""
        ...

    async def attach_skip_decision(
        self, run_id: UUID, payload: SkipDecisionPayload
    ) -> SkipDecisionResponse:
        """Submit a redacted zero-commit provider observation."""
        ...


__all__ = (
    "CollectionInvocation",
    "CollectorControlPlane",
    "CollectorWorkflowError",
    "CommandSecrets",
    "PageCursor",
    "SourceExecution",
)
