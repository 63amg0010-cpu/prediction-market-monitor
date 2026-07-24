"""Server-derived command completion and publication planning."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, assert_never

from app.domain.enums import CommandStatus, RunStatus

from .base import (
    CollectionError,
    CollectionErrorCode,
    canonical_json_hash,
    require_utc,
    token_matches,
)
from .commands import CommandState, aggregate_command_status
from .completion_failure import verify_failure
from .completion_models import (
    CompletionContext,
    CompletionPlan,
    CompletionRequest,
    CompletionSourceOutcome,
    PublicationDraft,
    RunCompletionFacts,
    completion_request_hash,
)
from .page_commit import page_chain_link

if TYPE_CHECKING:
    from datetime import datetime

    from app.domain.types import JsonValue

    from .checkpoint import RunState


def prepare_completion(
    context: CompletionContext,
    request: CompletionRequest,
) -> CompletionPlan:
    """Validate every run first and derive one all-or-nothing finalization."""
    valid_command = (
        context.command.status is CommandStatus.RUNNING
        and context.command.attempt == request.attempt
        and token_matches(request.lease_token, context.command.lease_hash)
    )
    if not valid_command:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    return prepare_server_completion(
        context,
        request.source_outcomes,
        completion_request_hash(request),
    )


def prepare_server_completion(
    context: CompletionContext,
    source_outcomes: tuple[CompletionSourceOutcome, ...],
    request_hash: str,
) -> CompletionPlan:
    """Apply the same persisted-fact rules after server-owned authentication."""
    now = require_utc(context.db_now)
    if context.command.status is not CommandStatus.RUNNING:
        raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
    facts_by_id = {facts.run.id: facts for facts in context.runs}
    outcomes_by_id = {outcome.run_id: outcome for outcome in source_outcomes}
    if (
        len(facts_by_id) != len(context.runs)
        or len(outcomes_by_id) != len(source_outcomes)
        or facts_by_id.keys() != outcomes_by_id.keys()
    ):
        raise CollectionError(CollectionErrorCode.RUN_SET_MISMATCH, 409)
    finalized: list[RunState] = []
    publications: list[PublicationDraft] = []
    for facts in sorted(context.runs, key=lambda item: item.run.id.hex):
        outcome = outcomes_by_id[facts.run.id]
        updated, publication = _prepare_run(context.command, facts, outcome, now)
        finalized.append(updated)
        if publication is not None:
            publications.append(publication)
    statuses = tuple(run.status for run in finalized)
    return CompletionPlan(
        request_hash,
        aggregate_command_status(statuses),
        tuple(finalized),
        tuple(publications),
        now,
    )


def _prepare_run(
    command: CommandState,
    facts: RunCompletionFacts,
    outcome: CompletionSourceOutcome,
    now: datetime,
) -> tuple[RunState, PublicationDraft | None]:
    run = facts.run
    if (
        run.command_id != command.id
        or run.attempt != command.attempt
        or run.status is not RunStatus.RUNNING
    ):
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    match outcome.terminal_status:
        case RunStatus.SUCCEEDED:
            if run.terminal_page_commit_id is None:
                raise CollectionError(CollectionErrorCode.TERMINAL_PAGE_MISSING, 409)
            _verify_persisted_chain(facts, outcome)
            publication = _success_publication(facts, outcome)
        case RunStatus.SKIPPED_POLICY | RunStatus.SKIPPED_QUOTA:
            _verify_persisted_chain(facts, outcome)
            _verify_skip(facts, outcome)
            publication = None
        case RunStatus.FAILED_RETRYABLE | RunStatus.FAILED_TERMINAL:
            _verify_persisted_chain(facts, outcome)
            verify_failure(facts, outcome)
            publication = None
        case RunStatus.CREATED | RunStatus.RUNNING | RunStatus.STALE_ABANDONED:
            raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
        case _:
            assert_never(outcome.terminal_status)
    failure = outcome.failure
    return (
        replace(
            run,
            status=outcome.terminal_status,
            failure_class=failure.failure_class.value if failure else None,
            failure_code=failure.code if failure else None,
            failure_fingerprint=failure.fingerprint if failure else None,
            failure_observed_at=require_utc(failure.observed_at) if failure else None,
            retry_after_at=require_utc(failure.retry_after_at)
            if failure and failure.retry_after_at
            else None,
            finalized_at=now,
            finished_at=now,
        ),
        publication,
    )


def _verify_persisted_chain(
    facts: RunCompletionFacts,
    outcome: CompletionSourceOutcome,
) -> None:
    run = facts.run
    chain = run.genesis_chain_hash
    commits = tuple(sorted(facts.commits, key=lambda item: item.page_ordinal))
    for ordinal, commit in enumerate(commits):
        expected_link = page_chain_link(chain, commit.page_content_hash)
        valid = (
            commit.run_id == run.id
            and commit.page_ordinal == ordinal
            and commit.previous_chain_hash == chain
            and commit.resulting_chain_hash == expected_link
        )
        if not valid:
            raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)
        chain = expected_link
    last = commits[-1] if commits else None
    comparisons_match = (
        outcome.committed_page_count == len(commits) == run.committed_page_count
        and outcome.committed_page_hash_chain == chain == run.committed_page_hash_chain
        and outcome.last_page_commit_id == (last.id if last else None)
        and outcome.final_page_ordinal == (last.page_ordinal if last else None)
        and outcome.final_cursor
        == facts.checkpoint.cursor
        == (last.next_cursor if last else run.start_cursor)
        and run.page_reservation_id is None
    )
    if not comparisons_match:
        raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)


def _success_publication(
    facts: RunCompletionFacts,
    outcome: CompletionSourceOutcome,
) -> PublicationDraft:
    run = facts.run
    if run.terminal_page_commit_id is None or not facts.commits:
        raise CollectionError(CollectionErrorCode.TERMINAL_PAGE_MISSING, 409)
    terminal_matches = (
        outcome.last_page_commit_id == run.terminal_page_commit_id
        and outcome.final_page_ordinal == run.terminal_page_ordinal
        and outcome.final_cursor == run.terminal_cursor
        and outcome.committed_page_hash_chain == run.terminal_chain_hash
        and facts.commits[-1].is_terminal_page
        and facts.commits[-1].terminal_reason == run.terminal_reason
        and facts.checkpoint.revision == facts.commits[-1].resulting_checkpoint_revision
        and outcome.skip_decision_id is None
        and outcome.failure is None
    )
    if not terminal_matches:
        raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)
    versions = sorted(
        {(item.id, item.content_hash) for item in facts.observed_post_versions},
        key=lambda item: str(item[0]),
    )
    values: list[JsonValue] = [
        {"content_hash": content_hash, "post_version_id": str(version_id)}
        for version_id, content_hash in versions
    ]
    return PublicationDraft(
        run.id,
        run.source_id,
        run.terminal_page_commit_id,
        run.terminal_chain_hash or run.committed_page_hash_chain,
        canonical_json_hash(values),
        len(versions),
        not versions,
    )


def _verify_skip(facts: RunCompletionFacts, outcome: CompletionSourceOutcome) -> None:
    proof = facts.skip_decision
    valid = (
        proof is not None
        and proof.id == outcome.skip_decision_id
        and proof.terminal_status is outcome.terminal_status
        and not facts.commits
        and facts.run.terminal_page_commit_id is None
        and outcome.failure is None
    )
    if not valid:
        raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)


__all__ = (
    "CompletionContext",
    "CompletionRequest",
    "CompletionSourceOutcome",
    "RunCompletionFacts",
    "prepare_completion",
    "prepare_server_completion",
)
