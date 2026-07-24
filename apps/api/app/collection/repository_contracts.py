"""Collection repository configuration and atomic operation port."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from uuid import UUID

    from . import command_store, completion_store, page_service_models
    from .checkpoint import CheckpointReplay
    from .commands import CommandState
    from .completion_models import CompletionOutcome
    from .dispatch import ClaimCredentials
    from .page_commit import PageCommitOutcome
    from .skip_decision_models import SkipDecisionOperation, SkipDecisionOutcome
    from .slot_store import MaterializationOperation


@dataclass(frozen=True, slots=True)
class CollectionRepositoryConfig:
    """Immutable caps and retry material owned by the server."""

    page: page_service_models.PageCommitServiceConfig
    completion: completion_store.CompletionServiceConfig


class CollectionRepository(Protocol):
    """Atomic collector operations exposed to the HTTP boundary."""

    async def materialize(
        self, operation: MaterializationOperation
    ) -> tuple[UUID, ...]:
        """Materialize eligible slots."""
        ...

    async def reserve(self, operation: command_store.ReserveOperation) -> CommandState:
        """Reserve one command."""
        ...

    async def confirm(self, operation: command_store.ConfirmOperation) -> CommandState:
        """Confirm dispatch acceptance."""
        ...

    async def claim(
        self, operation: command_store.ClaimOperation
    ) -> command_store.ClaimResult:
        """Claim authorized source runs."""
        ...

    async def checkpoint(self, run_id: UUID) -> CheckpointReplay:
        """Read a locked checkpoint replay."""
        ...

    async def commit_page(
        self, operation: page_service_models.PageCommitOperation
    ) -> PageCommitOutcome:
        """Commit or replay one page."""
        ...

    async def heartbeat(
        self, command_id: UUID, credentials: ClaimCredentials
    ) -> CommandState:
        """Refresh one claimed command."""
        ...

    async def complete(
        self, operation: completion_store.CompletionOperation
    ) -> CompletionOutcome:
        """Complete one command atomically."""
        ...

    async def attach_skip_decision(
        self, operation: SkipDecisionOperation
    ) -> SkipDecisionOutcome:
        """Attach one server-derived zero-commit skip proof."""
        ...


__all__ = ("CollectionRepository", "CollectionRepositoryConfig")
