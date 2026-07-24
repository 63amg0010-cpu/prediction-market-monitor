"""Validate terminal failure facts before durable completion."""

from typing import assert_never

from app.domain.enums import RunStatus

from .base import CollectionError, CollectionErrorCode
from .completion_models import CompletionSourceOutcome, FailureClass, RunCompletionFacts

_RETRYABLE_CODES = frozenset(
    {"network", "stale_abandoned", "timeout", "provider_temporarily_unavailable"}
)
_TERMINAL_CODES = frozenset(
    {
        "normalization_invalid",
        "provider_contract_invalid",
        "provider_terminal_http_error",
        "stale_abandoned",
    }
)


def verify_failure(facts: RunCompletionFacts, outcome: CompletionSourceOutcome) -> None:
    """Reject completion facts that do not match the recorded failure."""
    failure = outcome.failure
    match outcome.terminal_status:
        case RunStatus.FAILED_RETRYABLE:
            valid = (
                failure is not None
                and failure.failure_class is FailureClass.RETRYABLE
                and failure.code in _RETRYABLE_CODES
            )
        case RunStatus.FAILED_TERMINAL:
            valid = (
                failure is not None
                and failure.failure_class is FailureClass.TERMINAL
                and failure.code in _TERMINAL_CODES
            )
        case (
            RunStatus.CREATED
            | RunStatus.RUNNING
            | RunStatus.SUCCEEDED
            | RunStatus.SKIPPED_POLICY
            | RunStatus.SKIPPED_QUOTA
            | RunStatus.STALE_ABANDONED
        ):
            raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
        case _:
            assert_never(outcome.terminal_status)
    if (
        facts.run.terminal_page_commit_id is not None
        or outcome.skip_decision_id is not None
        or not valid
    ):
        raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)
