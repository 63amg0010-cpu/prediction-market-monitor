"""Atomic persistence for server-derived collection completion plans."""

from dataclasses import replace
from datetime import timedelta
from typing import assert_never
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.publication_models import (
    SourcePublicationSequence,
    SourceRunPublicationManifest,
)
from app.domain.enums import CommandStatus, RunStatus

from .base import MAX_COMMAND_ATTEMPTS, CollectionError, CollectionErrorCode
from .commands import CommandState
from .completion_context_store import LockedCompletionContext
from .completion_models import (
    CompletionPlan,
    CompletionPublicationResponse,
)
from .dispatch import retry_delay_seconds
from .orm_state import apply_checkpoint_state, apply_command_state, apply_run_state


async def persist_completion_plan(
    session: AsyncSession,
    locked: LockedCompletionContext,
    plan: CompletionPlan,
    retry_jitter_key: bytes,
) -> tuple[CompletionPublicationResponse, ...]:
    """Apply one validated plan to its locked rows and publication sequence."""
    publications = await _persist_publications(session, plan)
    run_rows = {row.id: row for row in locked.run_rows}
    checkpoints = {row.source_id: row for row in locked.checkpoint_rows}
    facts_by_run = {facts.run.id: facts for facts in locked.domain.runs}
    for state in plan.runs:
        apply_run_state(run_rows[state.id], state)
        match state.status:
            case RunStatus.SUCCEEDED:
                apply_checkpoint_state(
                    checkpoints[state.source_id],
                    replace(
                        facts_by_run[state.id].checkpoint,
                        last_completed_run_id=state.id,
                    ),
                )
            case (
                RunStatus.FAILED_RETRYABLE
                | RunStatus.FAILED_TERMINAL
                | RunStatus.SKIPPED_POLICY
                | RunStatus.SKIPPED_QUOTA
            ):
                pass
            case RunStatus.CREATED | RunStatus.RUNNING | RunStatus.STALE_ABANDONED:
                raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
            case _:
                assert_never(state.status)
    command = replace(
        locked.domain.command,
        status=plan.command_status,
        completed_at=plan.completed_at,
        outcome_code=plan.command_status.value,
    )
    apply_command_state(
        locked.command_row,
        _with_retry_availability(command, plan, retry_jitter_key),
    )
    return publications


def _with_retry_availability(
    command: CommandState,
    plan: CompletionPlan,
    retry_jitter_key: bytes,
) -> CommandState:
    match plan.command_status:
        case CommandStatus.FAILED_RETRYABLE:
            available_at = command.available_at
            if command.attempt < MAX_COMMAND_ATTEMPTS:
                available_at = plan.completed_at + timedelta(
                    seconds=retry_delay_seconds(
                        retry_jitter_key,
                        command.id,
                        command.attempt + 1,
                    )
                )
            return replace(
                command,
                available_at=available_at,
                reservation_started_at=None,
                reservation_nonce_hash=None,
                dispatched_at=None,
                claimed_at=None,
                heartbeat_at=None,
                lease_hash=None,
                github_run_id=None,
                github_run_attempt=None,
            )
        case (
            CommandStatus.SUCCEEDED
            | CommandStatus.PARTIAL
            | CommandStatus.SKIPPED
            | CommandStatus.FAILED_TERMINAL
        ):
            return command
        case (
            CommandStatus.QUEUED
            | CommandStatus.DISPATCH_RESERVED
            | CommandStatus.DISPATCHED
            | CommandStatus.RUNNING
            | CommandStatus.STALE_ABANDONED
        ):
            raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
        case _:
            assert_never(plan.command_status)


async def _persist_publications(
    session: AsyncSession,
    plan: CompletionPlan,
) -> tuple[CompletionPublicationResponse, ...]:
    responses: list[CompletionPublicationResponse] = []
    for draft in plan.publications:
        sequence_row = (
            await session.execute(
                select(SourcePublicationSequence)
                .where(SourcePublicationSequence.source_id == draft.source_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if sequence_row is None:
            sequence_row = SourcePublicationSequence(
                id=uuid4(),
                source_id=draft.source_id,
                current_sequence=0,
                updated_at=plan.completed_at,
            )
            session.add(sequence_row)
        sequence_row.current_sequence += 1
        sequence_row.updated_at = plan.completed_at
        session.add(
            SourceRunPublicationManifest(
                id=uuid4(),
                run_id=draft.run_id,
                source_id=draft.source_id,
                terminal_page_commit_id=draft.terminal_page_commit_id,
                sequence=sequence_row.current_sequence,
                final_chain_hash=draft.final_chain_hash,
                post_set_hash=draft.post_set_hash,
                distinct_post_version_count=draft.distinct_post_version_count,
                zero_post=draft.zero_post,
                committed_at=plan.completed_at,
            )
        )
        responses.append(
            CompletionPublicationResponse(
                run_id=draft.run_id,
                source_id=draft.source_id,
                sequence=sequence_row.current_sequence,
                post_set_hash=draft.post_set_hash,
                zero_post=draft.zero_post,
            )
        )
    return tuple(responses)


__all__ = ("persist_completion_plan",)
