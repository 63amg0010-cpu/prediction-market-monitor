"""One claimed source run's fetch, commit, recovery, and heartbeat loop."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapters.http_errors import AdapterHttpError
from .collector_contracts import CollectorWorkflowError, PageCursor
from .collector_outcomes import skip_outcome, success_outcome
from .collector_run_steps import (
    PageStepContext,
    build_page_request,
    checkpoint_cursor,
    commit_page,
    fetch_failure_outcome,
    post_page_outcome,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from uuid import UUID

    from app.api.routes.collector import ClaimedRunResponse

    from .collector_contracts import CollectorControlPlane, SourceExecution
    from .completion_models import CompletionSourceOutcome


async def collect_run(  # noqa: PLR0913 - exact run execution inputs.
    control: CollectorControlPlane,
    command_id: UUID,
    attempt: int,
    lease_token: str,
    run: ClaimedRunResponse,
    source: SourceExecution,
    clock: Callable[[], datetime],
) -> CompletionSourceOutcome:
    """Advance one run only from persisted checkpoint and commit receipts."""
    precomputed = _precomputed_outcome(run)
    if precomputed is not None:
        return precomputed
    state = await _load_initial_state(control, run)
    reviewed_page_cap = run.reviewed_page_cap
    if state.committed_page_count == reviewed_page_cap:
        return success_outcome(run.run_id, state)
    _ = await control.heartbeat(command_id, attempt, lease_token)
    while True:
        context = PageStepContext(
            command_id=command_id,
            attempt=attempt,
            lease_token=lease_token,
            run=run,
            source=source,
            state=state,
            clock=clock,
        )
        started_at = clock()
        try:
            page = await source.fetch_page(state)
        except AdapterHttpError as error:
            return await fetch_failure_outcome(control, context, error)
        finished_at = clock()
        request, reaches_page_cap = build_page_request(
            context,
            page,
            started_at,
            finished_at,
            reviewed_page_cap,
        )
        state, committed = await commit_page(
            control,
            context,
            request,
        )
        if not committed:
            continue
        _ = await control.heartbeat(command_id, attempt, lease_token)
        if reaches_page_cap:
            return success_outcome(run.run_id, state)
        outcome = post_page_outcome(run.run_id, state, page, finished_at)
        if outcome is not None:
            return outcome


def _precomputed_outcome(
    run: ClaimedRunResponse,
) -> CompletionSourceOutcome | None:
    if run.precomputed_terminal_status is None or run.skip_decision_id is None:
        return None
    state = PageCursor(
        revision=run.start_checkpoint_revision,
        cursor=run.start_cursor,
        ordinal=run.next_page_ordinal,
        committed_page_count=run.committed_page_count,
        committed_page_hash_chain=run.committed_page_hash_chain,
    )
    return skip_outcome(
        run.run_id,
        state,
        run.precomputed_terminal_status,
        run.skip_decision_id,
    )


async def _load_initial_state(
    control: CollectorControlPlane,
    run: ClaimedRunResponse,
) -> PageCursor:
    checkpoint = await control.checkpoint(run.run_id)
    if (
        checkpoint.next_page_ordinal != run.committed_page_count
        or checkpoint.committed_page_hash_chain != run.committed_page_hash_chain
    ):
        error_code = "checkpoint_run_mismatch"
        raise CollectorWorkflowError(error_code)
    state = checkpoint_cursor(checkpoint, run.committed_page_count)
    if run.reviewed_page_cap < 1 or state.committed_page_count > run.reviewed_page_cap:
        error_code = "invalid_reviewed_page_cap"
        raise CollectorWorkflowError(error_code)
    return state


__all__ = ("collect_run",)
