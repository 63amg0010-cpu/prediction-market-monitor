"""Pure planning for stale collection-command finalization."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import TYPE_CHECKING, assert_never

from app.domain.enums import CommandStatus, RunStatus

from .base import CollectionError, CollectionErrorCode
from .completion import prepare_server_completion
from .completion_models import (
    CompletionContext,
    CompletionPlan,
    CompletionSourceOutcome,
    FailureClass,
    FailureDetail,
    RunCompletionFacts,
    completion_outcomes_hash,
)
from .dispatch import StaleCheck, mark_stale

if TYPE_CHECKING:
    from datetime import datetime

    from .commands import CommandState


def prepare_stale_recovery(
    context: CompletionContext,
    retry_jitter_key: bytes,
) -> CompletionPlan | None:
    """Derive terminal outcomes from locked facts without inventing markers."""
    _require_recoverable_status(context.command.status)
    if not context.runs:
        return None
    ready = tuple(facts.run.completion_ready_at is not None for facts in context.runs)
    released = None if all(ready) else _release_stale(context, retry_jitter_key)
    if not all(ready) and released is None:
        return None
    released_status, retry_after_at = _released_facts(released)
    outcomes = tuple(
        _recovery_outcome(
            facts,
            released_status,
            context.db_now,
            retry_after_at,
        )
        for facts in context.runs
    )
    plan = prepare_server_completion(
        context,
        outcomes,
        completion_outcomes_hash(context.command.attempt, outcomes),
    )
    if released_status is CommandStatus.FAILED_RETRYABLE and any(ready):
        return replace(plan, command_status=CommandStatus.FAILED_RETRYABLE)
    return plan


def _require_recoverable_status(status: CommandStatus) -> None:
    match status:
        case CommandStatus.RUNNING | CommandStatus.STALE_ABANDONED:
            return
        case (
            CommandStatus.QUEUED
            | CommandStatus.DISPATCH_RESERVED
            | CommandStatus.DISPATCHED
            | CommandStatus.SUCCEEDED
            | CommandStatus.PARTIAL
            | CommandStatus.SKIPPED
            | CommandStatus.FAILED_RETRYABLE
            | CommandStatus.FAILED_TERMINAL
        ):
            raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
        case _:
            assert_never(status)


def _release_stale(
    context: CompletionContext,
    retry_jitter_key: bytes,
) -> CommandState | None:
    check = StaleCheck(
        db_now=context.db_now,
        completion_ready=False,
        retry_jitter_key=retry_jitter_key,
    )
    released = mark_stale(context.command, check)
    match released.status:
        case CommandStatus.RUNNING:
            return None
        case CommandStatus.STALE_ABANDONED:
            return mark_stale(released, check)
        case CommandStatus.FAILED_RETRYABLE | CommandStatus.FAILED_TERMINAL:
            return released
        case (
            CommandStatus.QUEUED
            | CommandStatus.DISPATCH_RESERVED
            | CommandStatus.DISPATCHED
            | CommandStatus.SUCCEEDED
            | CommandStatus.PARTIAL
            | CommandStatus.SKIPPED
        ):
            raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
        case _:
            assert_never(released.status)


def _released_facts(
    released: CommandState | None,
) -> tuple[CommandStatus | None, datetime | None]:
    if released is None:
        return None, None
    match released.status:
        case CommandStatus.FAILED_RETRYABLE:
            return released.status, released.available_at
        case CommandStatus.FAILED_TERMINAL:
            return released.status, None
        case (
            CommandStatus.QUEUED
            | CommandStatus.DISPATCH_RESERVED
            | CommandStatus.DISPATCHED
            | CommandStatus.RUNNING
            | CommandStatus.SUCCEEDED
            | CommandStatus.PARTIAL
            | CommandStatus.SKIPPED
            | CommandStatus.STALE_ABANDONED
        ):
            raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
        case _:
            assert_never(released.status)


def _recovery_outcome(
    facts: RunCompletionFacts,
    released_status: CommandStatus | None,
    observed_at: datetime,
    retry_after_at: datetime | None,
) -> CompletionSourceOutcome:
    run = facts.run
    if run.completion_ready_at is not None:
        terminal_status = RunStatus.SUCCEEDED
        failure = None
    else:
        match released_status:
            case CommandStatus.FAILED_RETRYABLE:
                terminal_status = RunStatus.FAILED_RETRYABLE
                failure_class = FailureClass.RETRYABLE
            case CommandStatus.FAILED_TERMINAL:
                terminal_status = RunStatus.FAILED_TERMINAL
                failure_class = FailureClass.TERMINAL
            case (
                None
                | CommandStatus.QUEUED
                | CommandStatus.DISPATCH_RESERVED
                | CommandStatus.DISPATCHED
                | CommandStatus.RUNNING
                | CommandStatus.SUCCEEDED
                | CommandStatus.PARTIAL
                | CommandStatus.SKIPPED
                | CommandStatus.STALE_ABANDONED
            ):
                raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
            case _:
                assert_never(released_status)
        fingerprint = sha256(
            f"{run.command_id}:{run.attempt}:stale".encode()
        ).hexdigest()
        failure = FailureDetail.model_validate(
            {
                "class": failure_class,
                "code": "stale_abandoned",
                "fingerprint": fingerprint,
                "observed_at": observed_at,
                "retry_after_at": retry_after_at,
            }
        )
    return CompletionSourceOutcome(
        run_id=run.id,
        terminal_status=terminal_status,
        last_page_commit_id=run.last_page_commit_id,
        final_cursor=run.final_cursor,
        final_page_ordinal=run.final_page_ordinal,
        committed_page_count=run.committed_page_count,
        committed_page_hash_chain=run.committed_page_hash_chain,
        skip_decision_id=None,
        failure=failure,
    )
