"""Transactional SQLAlchemy repository for collector control-plane operations."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - retained for repository annotations.
from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003 - retained for repository annotations.

from sqlalchemy import DateTime, func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002 - repository port type.

from app.db.run_models import CollectionRun, SourceCheckpoint
from app.db.scheduler_models import CollectionCommand
from app.db.session import DatabaseSessions  # noqa: TC001 - repository port type.

from . import command_store, commands, completion_store, page_service_models
from .authorization import require_active_authorization
from .authorization_store import (
    authorization_snapshots,
    claim_authorization_statement,
)
from .base import CollectionError, CollectionErrorCode
from .checkpoint import CheckpointReplay, checkpoint_replay
from .claim_policy_store import claim_source_policies
from .completion_models import CompletionOutcome  # noqa: TC001 - repository port type.
from .dispatch import ClaimCredentials, heartbeat_command
from .orm_state import (
    apply_command_state,
    to_checkpoint_state,
    to_command_state,
    to_run_state,
)
from .page_commit import PageCommitOutcome  # noqa: TC001 - repository port type.
from .page_store import execute_page_commit
from .reconciliation_store import recover_due_commands
from .repository_contracts import CollectionRepository, CollectionRepositoryConfig
from .skip_decision_store import attach_skip_decision
from .slot_store import MaterializationOperation, materialize_slots

if TYPE_CHECKING:
    from .skip_decision_models import SkipDecisionOperation, SkipDecisionOutcome


class SqlAlchemyCollectionRepository:
    """Own one database transaction for every collector mutation or replay read."""

    def __init__(
        self, sessions: DatabaseSessions, config: CollectionRepositoryConfig
    ) -> None:
        """Bind the session factory and server-owned limits."""
        self._sessions: DatabaseSessions = sessions
        self._config: CollectionRepositoryConfig = config

    async def materialize(
        self, operation: MaterializationOperation
    ) -> tuple[UUID, ...]:
        """Materialize all database-time eligible collection slots atomically."""
        async with self._sessions.open() as session, session.begin():
            _ = await materialize_slots(session, operation)
            return await recover_due_commands(
                session,
                operation.scope_version,
                self._config.completion.retry_jitter_key,
            )

    async def reserve(
        self, operation: command_store.ReserveOperation
    ) -> commands.CommandState:
        """Reserve one due command under its row lock."""
        async with self._sessions.open() as session, session.begin():
            return await command_store.reserve_command(session, operation)

    async def confirm(
        self, operation: command_store.ConfirmOperation
    ) -> commands.CommandState:
        """Persist GitHub dispatch acceptance under the command lock."""
        async with self._sessions.open() as session, session.begin():
            return await command_store.confirm_command(session, operation)

    async def claim(
        self, operation: command_store.ClaimOperation
    ) -> command_store.ClaimResult:
        """Authorize every exact source scope before creating claimed runs."""
        async with self._sessions.open() as session, session.begin():
            command = await self._locked_command(session, operation.command_id)
            now = await _database_now(session)
            policies = await claim_source_policies(
                session,
                operation.source_ids,
                command.scope_version,
                now,
                self._config.page,
            )
            return await command_store.claim_runs(session, operation, policies)

    async def checkpoint(self, run_id: UUID) -> CheckpointReplay:
        """Return only the locked authorized persisted replay position."""
        async with self._sessions.open() as session, session.begin():
            run = (
                await session.execute(
                    select(CollectionRun)
                    .where(CollectionRun.id == run_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if run is None:
                raise CollectionError(
                    CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409
                )
            await _require_run_authorization(session, run, await _database_now(session))
            checkpoint = (
                await session.execute(
                    select(SourceCheckpoint)
                    .where(
                        SourceCheckpoint.source_id == run.source_id,
                        SourceCheckpoint.scope_version == run.scope_version,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            return checkpoint_replay(to_run_state(run), to_checkpoint_state(checkpoint))

    async def commit_page(
        self, operation: page_service_models.PageCommitOperation
    ) -> PageCommitOutcome:
        """Commit or replay one page with the existing row-lock and CAS planner."""
        async with self._sessions.open() as session, session.begin():
            return await execute_page_commit(session, operation, self._config.page)

    async def heartbeat(
        self, command_id: UUID, credentials: ClaimCredentials
    ) -> commands.CommandState:
        """Advance command and active-run heartbeats using database time."""
        async with self._sessions.open() as session, session.begin():
            command_row = await self._locked_command(session, command_id)
            run_rows = tuple(
                (
                    await session.execute(
                        select(CollectionRun)
                        .where(
                            CollectionRun.command_id == command_id,
                            CollectionRun.attempt == command_row.attempt,
                        )
                        .order_by(CollectionRun.source_id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            state = heartbeat_command(
                to_command_state(command_row, tuple(row.source_id for row in run_rows)),
                credentials,
                await _database_now(session),
            )
            apply_command_state(command_row, state)
            for row in run_rows:
                row.heartbeat_at = state.heartbeat_at
            return state

    async def complete(
        self, operation: completion_store.CompletionOperation
    ) -> CompletionOutcome:
        """Validate, terminalize, publish, and receipt a command atomically."""
        async with self._sessions.open() as session, session.begin():
            return await completion_store.execute_completion(
                session, operation, self._config.completion
            )

    async def attach_skip_decision(
        self, operation: SkipDecisionOperation
    ) -> SkipDecisionOutcome:
        """Attach a server-derived skip proof in one locked transaction."""
        async with self._sessions.open() as session, session.begin():
            return await attach_skip_decision(session, operation)

    @staticmethod
    async def _locked_command(
        session: AsyncSession, command_id: UUID
    ) -> CollectionCommand:
        row = (
            await session.execute(
                select(CollectionCommand)
                .where(CollectionCommand.id == command_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
        return row


async def _database_now(session: AsyncSession) -> datetime:
    clock = func.clock_timestamp(type_=DateTime(timezone=True))
    return (await session.execute(select(clock))).scalar_one()


async def _require_run_authorization(
    session: AsyncSession, run: CollectionRun, now: datetime
) -> None:
    snapshots = await authorization_snapshots(
        session, (run.source_id,), run.scope_version
    )
    snapshot = snapshots.get(run.source_id)
    if snapshot is None or snapshot.decision_id != run.authorization_decision_id:
        raise CollectionError(CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403)
    _ = require_active_authorization(snapshot, run.source_id, run.scope_version, now)


__all__ = (
    "CollectionRepository",
    "CollectionRepositoryConfig",
    "SqlAlchemyCollectionRepository",
    "claim_authorization_statement",
)
