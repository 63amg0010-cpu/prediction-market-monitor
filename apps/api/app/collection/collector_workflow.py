"""Durable collector orchestration facade and command-level workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapters.models import PreflightBlocked, SourceBlockedError
from .collector_contracts import (
    CollectionInvocation,
    CollectorControlPlane,
    CollectorWorkflowError,
    CommandSecrets,
    PageCursor,
    SourceExecution,
)
from .collector_run import collect_run
from .completion_models import CompletionRequest

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from .completion_models import CompletionResponse


async def run_collection_workflow(
    control: CollectorControlPlane,
    invocation: CollectionInvocation,
    sources: tuple[SourceExecution, ...],
    secret_factory: Callable[[], CommandSecrets],
    clock: Callable[[], datetime],
) -> tuple[CompletionResponse, ...]:
    """Run each durable command through claim, pages, and completion."""
    await control.authenticate()
    source_map = {source.source_id: source for source in sources}
    if len(source_map) != len(sources) or source_map.keys() != set(
        invocation.source_ids
    ):
        error_code = "collector_source_set_mismatch"
        raise CollectorWorkflowError(error_code)
    for source in sources:
        preflight = source.preflight()
        if isinstance(preflight, PreflightBlocked):
            raise SourceBlockedError(source.platform, preflight)
    command_ids = (
        (invocation.command_id,)
        if invocation.command_id is not None
        else await control.materialize(
            invocation.scope_version, invocation.deployment_activation_at
        )
    )
    completions: list[CompletionResponse] = []
    for command_id in command_ids:
        secrets = secret_factory()
        reserved = await control.reserve(
            command_id, secrets.reservation_nonce, secrets.lease_token
        )
        attempt = reserved.attempt
        _ = await control.confirm(
            command_id,
            attempt,
            secrets.reservation_nonce,
            invocation.github_run_id,
            invocation.github_run_attempt,
        )
        claimed = await control.claim(
            command_id,
            attempt,
            secrets.lease_token,
            secrets.reservation_nonce,
            invocation.source_ids,
        )
        runs = {run.source_id: run for run in claimed.runs}
        if runs.keys() != set(invocation.source_ids):
            error_code = "claimed_run_set_mismatch"
            raise CollectorWorkflowError(error_code)
        for source_id, run in runs.items():
            supplied = source_map[source_id].authorization
            if supplied is not None and supplied != run.authorization:
                error_code = "claimed_authorization_snapshot_mismatch"
                raise CollectorWorkflowError(error_code)
        outcomes = [
            await collect_run(
                control,
                command_id,
                attempt,
                secrets.lease_token,
                runs[source_id],
                source_map[source_id],
                clock,
            )
            for source_id in invocation.source_ids
        ]
        completed = await control.complete(
            command_id,
            CompletionRequest(
                completion_idempotency_key=secrets.completion_idempotency_key,
                attempt=attempt,
                lease_token=secrets.lease_token,
                source_outcomes=tuple(outcomes),
            ),
        )
        completions.append(completed)
    return tuple(completions)


__all__ = (
    "CollectionInvocation",
    "CollectorControlPlane",
    "CollectorWorkflowError",
    "CommandSecrets",
    "PageCursor",
    "SourceExecution",
    "run_collection_workflow",
)
