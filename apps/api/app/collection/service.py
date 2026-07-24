"""Transactional collection control-plane entrypoints."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .claim_policy_store import ClaimSourcePolicy
from .command_store import (
    ClaimOperation,
    ClaimResult,
    ConfirmOperation,
    ReserveOperation,
    claim_runs,
    confirm_command,
    reserve_command,
)
from .commands import CommandState
from .completion_models import CompletionOutcome
from .completion_store import (
    CompletionOperation,
    CompletionServiceConfig,
    execute_completion,
)
from .page_commit import PageCommitOutcome
from .page_service_models import PageCommitOperation, PageCommitServiceConfig
from .page_store import execute_page_commit
from .slot_store import MaterializationOperation, materialize_slots


async def materialize_collection_slots(
    session: AsyncSession,
    operation: MaterializationOperation,
) -> tuple[UUID, ...]:
    """Atomically materialize every database-time eligible grid slot."""
    async with session.begin():
        return await materialize_slots(session, operation)


async def reserve_collection_command(
    session: AsyncSession,
    operation: ReserveOperation,
) -> CommandState:
    """Atomically reserve one due collection attempt."""
    async with session.begin():
        return await reserve_command(session, operation)


async def confirm_collection_dispatch(
    session: AsyncSession,
    operation: ConfirmOperation,
) -> CommandState:
    """Atomically bind confirmed GitHub dispatch facts."""
    async with session.begin():
        return await confirm_command(session, operation)


async def claim_collection_runs(
    session: AsyncSession,
    operation: ClaimOperation,
    policies: dict[UUID, ClaimSourcePolicy],
) -> ClaimResult:
    """Atomically claim a command and create its source runs."""
    async with session.begin():
        return await claim_runs(session, operation, policies)


async def commit_collection_page(
    session: AsyncSession,
    operation: PageCommitOperation,
    config: PageCommitServiceConfig,
) -> PageCommitOutcome:
    """Atomically commit one authenticated lease-bound source page."""
    async with session.begin():
        return await execute_page_commit(session, operation, config)


async def complete_collection_command(
    session: AsyncSession,
    operation: CompletionOperation,
    config: CompletionServiceConfig,
) -> CompletionOutcome:
    """Atomically terminalize all runs and publish successful sources."""
    async with session.begin():
        return await execute_completion(session, operation, config)
