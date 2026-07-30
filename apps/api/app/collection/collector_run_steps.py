"""Fetch, commit, and terminalize one collector page step."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never
from uuid import uuid5

from app.api.routes.collector_models import SkipDecisionPayload
from app.domain.enums import TerminalReason

from .adapters.http_errors import AdapterHttpError, HttpFailureKind
from .adapters.models import PageTermination
from .base import CollectionError, CollectionErrorCode
from .collector_contracts import CollectorWorkflowError, PageCursor
from .collector_outcomes import (
    failure_outcome,
    rate_limit_outcome,
    skip_outcome,
    success_outcome,
)
from .collector_pages import page_request
from .page_commit import page_chain_link

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from uuid import UUID

    from app.api.routes.collector import CheckpointResponse, ClaimedRunResponse

    from .adapters.models import AdapterPage
    from .collector_contracts import CollectorControlPlane, SourceExecution
    from .completion_models import CompletionSourceOutcome
    from .page_commit import PageCommitRequest


@dataclass(frozen=True, slots=True)
class PageStepContext:
    """Immutable command, source, and cursor inputs for one page step."""

    command_id: UUID
    attempt: int
    lease_token: str
    run: ClaimedRunResponse
    source: SourceExecution
    state: PageCursor
    clock: Callable[[], datetime]


_REPLAYABLE_CONFLICTS = frozenset(
    {
        CollectionErrorCode.CHECKPOINT_CONFLICT,
        CollectionErrorCode.ORDINAL_GAP,
        CollectionErrorCode.ORDINAL_ALREADY_COMMITTED,
    }
)


def checkpoint_cursor(
    checkpoint: CheckpointResponse, committed_page_count: int
) -> PageCursor:
    """Build a cursor from the checkpoint returned by the server."""
    return PageCursor(
        revision=checkpoint.expected_checkpoint_revision,
        cursor=checkpoint.expected_cursor,
        ordinal=checkpoint.next_page_ordinal,
        accepted_count=checkpoint.accepted_count,
        committed_page_count=committed_page_count,
        committed_page_hash_chain=checkpoint.committed_page_hash_chain,
        last_page_commit_id=checkpoint.last_page_commit_id,
    )


async def fetch_failure_outcome(
    control: CollectorControlPlane,
    context: PageStepContext,
    error: AdapterHttpError,
) -> CompletionSourceOutcome:
    """Convert one provider failure into a skip or terminal outcome."""
    run = context.run
    source = context.source
    state = context.state
    if (
        state.committed_page_count == 0
        and error.classification.kind in (HttpFailureKind.POLICY, HttpFailureKind.QUOTA)
        and error.status_code in (401, 403, 429)
    ):
        skip = await control.attach_skip_decision(
            run.run_id,
            SkipDecisionPayload.model_validate(
                {
                    "command_id": context.command_id,
                    "attempt": context.attempt,
                    "lease_token": context.lease_token,
                    "idempotency_key": uuid5(
                        run.run_id,
                        ":".join(
                            (
                                str(context.attempt),
                                source.platform.value,
                                str(error.status_code),
                                error.request_path,
                                error.classification.code,
                            )
                        ),
                    ),
                    "provider": source.platform,
                    "route": error.request_path,
                    "http_status": error.status_code,
                    "failure_code": error.classification.code,
                }
            ),
        )
        return skip_outcome(
            run.run_id,
            state,
            skip.terminal_status,
            skip.skip_decision_id,
        )
    return failure_outcome(run.run_id, state, error, context.clock())


def build_page_request(
    context: PageStepContext,
    page: AdapterPage,
    started_at: datetime,
    finished_at: datetime,
    reviewed_page_cap: int,
) -> tuple[PageCommitRequest, bool]:
    """Build one page request and apply the server-reviewed cap marker."""
    request = page_request(
        context.command_id,
        context.attempt,
        context.lease_token,
        context.run.run_id,
        context.state,
        page,
        started_at,
        finished_at,
    )
    reaches_page_cap = context.state.committed_page_count + 1 == reviewed_page_cap
    if reaches_page_cap and request.terminal_reason is None:
        request = request.model_copy(
            update={
                "is_terminal_page": True,
                "terminal_reason": TerminalReason.REVIEWED_PAGE_CAP,
            }
        )
    return request, reaches_page_cap


async def commit_page(
    control: CollectorControlPlane,
    context: PageStepContext,
    request: PageCommitRequest,
) -> tuple[PageCursor, bool]:
    """Commit one page or replay the newer server checkpoint after conflict."""
    try:
        receipt = await control.commit_page(context.run.run_id, request)
    except CollectionError as error:
        checkpoint = await control.checkpoint(context.run.run_id)
        if error.code not in _REPLAYABLE_CONFLICTS:
            raise
        recovered = checkpoint_cursor(checkpoint, checkpoint.next_page_ordinal)
        if recovered == context.state:
            error_code = "checkpoint_conflict_not_advanced"
            raise CollectorWorkflowError(error_code) from error
        _ = await control.heartbeat(
            context.command_id, context.attempt, context.lease_token
        )
        return recovered, False
    if (
        receipt.next_cursor != request.next_cursor
        or receipt.checkpoint_revision != context.state.revision + 1
    ):
        error_code = "page_commit_receipt_mismatch"
        raise CollectorWorkflowError(error_code)
    next_state = PageCursor(
        revision=receipt.checkpoint_revision,
        cursor=receipt.next_cursor,
        ordinal=context.state.ordinal + 1,
        accepted_count=context.state.accepted_count + receipt.accepted_count,
        committed_page_count=context.state.committed_page_count + 1,
        committed_page_hash_chain=page_chain_link(
            context.state.committed_page_hash_chain, receipt.page_content_hash
        ),
        last_page_commit_id=receipt.page_commit_id,
    )
    return next_state, True


def post_page_outcome(
    run_id: UUID,
    state: PageCursor,
    page: AdapterPage,
    finished_at: datetime,
) -> CompletionSourceOutcome | None:
    """Return a terminal outcome when the provider ended the page stream."""
    match page.termination:  # noqa: RUF100  # noqa: MATCH_OK
        case PageTermination.CONTINUE:
            return None
        case (
            PageTermination.SOURCE_EXHAUSTED
            | PageTermination.REVIEWED_POST_CAP
            | PageTermination.REVIEWED_BYTE_CAP
        ):
            return success_outcome(run_id, state)
        case PageTermination.RATE_LIMIT_PAUSE:
            return rate_limit_outcome(run_id, state, page, finished_at)
    assert_never(page.termination)


__all__ = (
    "build_page_request",
    "checkpoint_cursor",
    "commit_page",
    "fetch_failure_outcome",
    "post_page_outcome",
)
