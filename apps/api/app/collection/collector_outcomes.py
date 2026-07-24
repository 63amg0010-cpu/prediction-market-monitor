"""Server-verifiable collector completion outcome construction."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, assert_never

from app.domain.enums import RunStatus

from .adapters.http_errors import HttpFailureKind
from .completion_models import (
    CompletionSourceOutcome,
    FailureClass,
    FailureDetail,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from .adapters.http_errors import AdapterHttpError
    from .adapters.models import AdapterPage
    from .collector_contracts import PageCursor


def success_outcome(run_id: UUID, state: PageCursor) -> CompletionSourceOutcome:
    """Return a successful outcome bound to the last persisted receipt."""
    return CompletionSourceOutcome(
        run_id=run_id,
        terminal_status=RunStatus.SUCCEEDED,
        last_page_commit_id=state.last_page_commit_id,
        final_cursor=state.cursor,
        final_page_ordinal=state.ordinal - 1,
        committed_page_count=state.committed_page_count,
        committed_page_hash_chain=state.committed_page_hash_chain,
        skip_decision_id=None,
        failure=None,
    )


def failure_outcome(
    run_id: UUID,
    state: PageCursor,
    error: AdapterHttpError,
    observed_at: datetime,
) -> CompletionSourceOutcome:
    """Map redacted HTTP classification to a typed terminal outcome."""
    classification = error.classification
    retryable = classification.kind in (
        HttpFailureKind.RETRYABLE,
        HttpFailureKind.QUOTA,
    )
    terminal_status = (
        RunStatus.FAILED_RETRYABLE if retryable else RunStatus.FAILED_TERMINAL
    )
    failure_class = FailureClass.RETRYABLE if retryable else FailureClass.TERMINAL
    retry_at = None
    if classification.retry_after_seconds is not None:
        retry_at = observed_at + timedelta(
            seconds=float(classification.retry_after_seconds)
        )
    code = _failure_code(classification.kind, classification.code)
    failure = FailureDetail.model_validate(
        {
            "class": failure_class,
            "code": code,
            "fingerprint": sha256(f"{run_id}:{code}".encode()).hexdigest(),
            "observed_at": observed_at,
            "retry_after_at": retry_at,
        }
    )
    return _terminal_failure(run_id, state, terminal_status, failure)


def _failure_code(kind: HttpFailureKind, code: str) -> str:
    match kind:  # noqa: RUF100  # noqa: MATCH_OK
        case HttpFailureKind.POLICY:
            return "provider_terminal_http_error"
        case HttpFailureKind.QUOTA:
            return "provider_temporarily_unavailable"
        case HttpFailureKind.RETRYABLE | HttpFailureKind.TERMINAL:
            return code
    assert_never(kind)


def skip_outcome(
    run_id: UUID,
    state: PageCursor,
    terminal_status: RunStatus,
    skip_decision_id: UUID,
) -> CompletionSourceOutcome:
    """Return a server-proof-bound zero-commit skip outcome."""
    return CompletionSourceOutcome(
        run_id=run_id,
        terminal_status=terminal_status,
        last_page_commit_id=state.last_page_commit_id,
        final_cursor=state.cursor,
        final_page_ordinal=None,
        committed_page_count=state.committed_page_count,
        committed_page_hash_chain=state.committed_page_hash_chain,
        skip_decision_id=skip_decision_id,
        failure=None,
    )


def rate_limit_outcome(
    run_id: UUID, state: PageCursor, page: AdapterPage, observed_at: datetime
) -> CompletionSourceOutcome:
    """Return a retryable provider pause with its authoritative retry time."""
    retry_at = None
    if page.rate_limit.retry_after_seconds is not None:
        retry_at = observed_at + timedelta(
            seconds=float(page.rate_limit.retry_after_seconds)
        )
    code = "provider_temporarily_unavailable"
    failure = FailureDetail.model_validate(
        {
            "class": FailureClass.RETRYABLE,
            "code": code,
            "fingerprint": sha256(f"{run_id}:{code}".encode()).hexdigest(),
            "observed_at": observed_at,
            "retry_after_at": retry_at,
        }
    )
    return _terminal_failure(run_id, state, RunStatus.FAILED_RETRYABLE, failure)


def _terminal_failure(
    run_id: UUID,
    state: PageCursor,
    status: RunStatus,
    failure: FailureDetail,
) -> CompletionSourceOutcome:
    return CompletionSourceOutcome(
        run_id=run_id,
        terminal_status=status,
        last_page_commit_id=state.last_page_commit_id,
        final_cursor=state.cursor,
        final_page_ordinal=(state.ordinal - 1 if state.committed_page_count else None),
        committed_page_count=state.committed_page_count,
        committed_page_hash_chain=state.committed_page_hash_chain,
        skip_decision_id=None,
        failure=failure,
    )


__all__ = (
    "failure_outcome",
    "rate_limit_outcome",
    "skip_outcome",
    "success_outcome",
)
