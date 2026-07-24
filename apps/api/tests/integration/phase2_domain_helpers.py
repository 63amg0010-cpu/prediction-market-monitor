"""Reusable in-memory scenarios for the Phase 2 contract nodes."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from app.collection.base import CollectionErrorCode
from app.collection.completion import CompletionContext, prepare_completion
from app.collection.completion_models import (
    CompletionPlan,
    CompletionRequest,
    CompletionSourceOutcome,
    ObservedPostVersion,
    RunCompletionFacts,
)
from app.collection.page_commit import (
    ExistingPostVersion,
    PageCommitContext,
    PageCommitPlan,
    PageCommitRequest,
    prepare_page_commit,
)
from app.domain.enums import RunStatus

from .phase2_fixtures import (
    LEASE,
    NOW,
    PageContextOverrides,
    PageRequestOverrides,
    accepted_post,
    oversize_post,
    page_context,
    page_request,
)


def facts_from_plan(
    plan: PageCommitPlan,
    observed: tuple[ObservedPostVersion, ...] = (),
) -> RunCompletionFacts:
    """Represent one page plan as the persisted facts completion reads."""
    return RunCompletionFacts(
        plan.updated_run,
        plan.updated_checkpoint,
        (plan.commit,),
        observed,
        None,
    )


def success_outcome(facts: RunCompletionFacts) -> CompletionSourceOutcome:
    """Build exact submitted values from persisted terminal facts."""
    commit = facts.commits[-1]
    return CompletionSourceOutcome(
        run_id=facts.run.id,
        terminal_status=RunStatus.SUCCEEDED,
        last_page_commit_id=commit.id,
        final_cursor=commit.next_cursor,
        final_page_ordinal=commit.page_ordinal,
        committed_page_count=len(facts.commits),
        committed_page_hash_chain=commit.resulting_chain_hash,
        skip_decision_id=None,
        failure=None,
    )


@dataclass(frozen=True, slots=True)
class ZeroPostScenario:
    """Three persisted item outcomes used by the publication manifest gate."""

    empty_plan: PageCommitPlan
    empty_completion: CompletionPlan
    duplicate_plan: PageCommitPlan
    duplicate_completion: CompletionPlan
    rejection_plan: PageCommitPlan
    rejection_completion: CompletionPlan


def build_zero_post_scenario() -> ZeroPostScenario:
    """Create empty, duplicate, and oversize-only terminal completions."""
    empty_context = page_context()
    empty_plan = prepare_page_commit(
        empty_context,
        page_request(empty_context),
        lambda: UUID(int=100),
    )
    duplicate = accepted_post()
    existing = ExistingPostVersion(
        duplicate.source_post_id,
        UUID(int=200),
        UUID(int=201),
        duplicate.content_hash,
        1,
    )
    duplicate_context = page_context(PageContextOverrides(existing_posts=(existing,)))
    duplicate_plan = prepare_page_commit(
        duplicate_context,
        page_request(
            duplicate_context,
            PageRequestOverrides(posts=(duplicate,)),
        ),
        lambda: UUID(int=300),
    )
    rejection_context = page_context()
    rejection = oversize_post()
    rejection_plan = prepare_page_commit(
        rejection_context,
        page_request(
            rejection_context,
            PageRequestOverrides(posts=(rejection,)),
        ),
        lambda: UUID(int=400),
    )
    empty_facts = facts_from_plan(empty_plan)
    duplicate_facts = facts_from_plan(
        duplicate_plan,
        (ObservedPostVersion(UUID(int=201), duplicate.content_hash),),
    )
    rejection_facts = facts_from_plan(rejection_plan)
    empty_completion = prepare_completion(
        CompletionContext(empty_context.command, (empty_facts,), NOW),
        CompletionRequest(
            completion_idempotency_key=UUID(int=904),
            attempt=1,
            lease_token=LEASE,
            source_outcomes=(success_outcome(empty_facts),),
        ),
    )
    duplicate_completion = prepare_completion(
        CompletionContext(duplicate_context.command, (duplicate_facts,), NOW),
        CompletionRequest(
            completion_idempotency_key=UUID(int=905),
            attempt=1,
            lease_token=LEASE,
            source_outcomes=(success_outcome(duplicate_facts),),
        ),
    )
    rejection_completion = prepare_completion(
        CompletionContext(rejection_context.command, (rejection_facts,), NOW),
        CompletionRequest(
            completion_idempotency_key=UUID(int=906),
            attempt=1,
            lease_token=LEASE,
            source_outcomes=(success_outcome(rejection_facts),),
        ),
    )
    return ZeroPostScenario(
        empty_plan,
        empty_completion,
        duplicate_plan,
        duplicate_completion,
        rejection_plan,
        rejection_completion,
    )


@dataclass(frozen=True, slots=True)
class PageConflictCase:
    """One page request and its expected server conflict code."""

    request: PageCommitRequest
    context: PageCommitContext
    code: CollectionErrorCode


def build_page_conflict_cases() -> tuple[PageConflictCase, ...]:
    """Build CAS, ordinal, lease, sealed, and duplicate-ordinal conflicts."""
    baseline = page_context()
    nonterminal = page_request(
        baseline,
        PageRequestOverrides(terminal_reason=None, next_cursor="cursor-0"),
    )
    committed = prepare_page_commit(baseline, nonterminal, lambda: UUID(int=1))
    terminal = prepare_page_commit(
        baseline,
        page_request(baseline),
        lambda: UUID(int=50),
    )
    sealed_context = replace(
        baseline,
        checkpoint=terminal.updated_checkpoint,
        run=terminal.updated_run,
    )
    sealed_request = page_request(
        sealed_context,
        PageRequestOverrides(
            expected_revision=1,
            expected_cursor="cursor-1",
            ordinal=1,
        ),
    )
    return (
        PageConflictCase(
            page_request(baseline, PageRequestOverrides(expected_revision=9)),
            baseline,
            CollectionErrorCode.CHECKPOINT_CONFLICT,
        ),
        PageConflictCase(
            page_request(baseline, PageRequestOverrides(ordinal=2)),
            baseline,
            CollectionErrorCode.ORDINAL_GAP,
        ),
        PageConflictCase(
            page_request(baseline).model_copy(update={"lease_token": "x" * 43}),
            baseline,
            CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH,
        ),
        PageConflictCase(
            sealed_request,
            sealed_context,
            CollectionErrorCode.RUN_STREAM_SEALED,
        ),
        PageConflictCase(
            page_request(baseline),
            replace(baseline, existing_ordinal_commit=committed.commit),
            CollectionErrorCode.ORDINAL_ALREADY_COMMITTED,
        ),
    )


__all__ = (
    "PageConflictCase",
    "ZeroPostScenario",
    "build_page_conflict_cases",
    "build_zero_post_scenario",
    "facts_from_plan",
    "success_outcome",
)
