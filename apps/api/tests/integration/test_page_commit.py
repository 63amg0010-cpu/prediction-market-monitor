"""Phase 2 page commit and terminal-marker acceptance gates."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from app.collection.base import (
    CollectionError,
    CollectionErrorCode,
    canonical_json_hash,
)
from app.collection.completion import CompletionContext, prepare_completion
from app.collection.completion_models import (
    CompletionRequest,
    CompletionSourceOutcome,
    RunCompletionFacts,
)
from app.collection.page_commit import (
    prepare_page_commit,
)
from app.domain.enums import AuthorizationStatus, RunStatus, TerminalReason

from .phase2_domain_helpers import (
    build_page_conflict_cases,
    build_zero_post_scenario,
    facts_from_plan,
    success_outcome,
)
from .phase2_fixtures import (
    LEASE,
    NOW,
    PageContextOverrides,
    PageRequestOverrides,
    accepted_post,
    page_context,
    page_request,
)


def test_success_requires_persisted_terminal_commit() -> None:
    # Given: a running source with no persisted terminal page.
    context = page_context()
    outcome = CompletionSourceOutcome(
        run_id=context.run.id,
        terminal_status=RunStatus.SUCCEEDED,
        last_page_commit_id=UUID(int=999),
        final_cursor=None,
        final_page_ordinal=0,
        committed_page_count=1,
        committed_page_hash_chain="0" * 64,
        skip_decision_id=None,
        failure=None,
    )

    # When: completion is prepared from only client-supplied terminal facts.
    with pytest.raises(CollectionError) as captured:
        _ = prepare_completion(
            CompletionContext(
                context.command,
                (
                    RunCompletionFacts(
                        context.run,
                        context.checkpoint,
                        (),
                        (),
                        None,
                    ),
                ),
                NOW,
            ),
            CompletionRequest(
                completion_idempotency_key=UUID(int=901),
                attempt=1,
                lease_token=LEASE,
                source_outcomes=(outcome,),
            ),
        )

    # Then: the server returns terminal_page_missing and changes no state.
    assert captured.value.code is CollectionErrorCode.TERMINAL_PAGE_MISSING
    assert context.command.status.value == "running"
    assert context.run.status is RunStatus.RUNNING


def test_terminal_binds_cursor_ordinal_chain() -> None:
    # Given: a persisted empty terminal page and its server-derived outcome.
    context = page_context()
    plan = prepare_page_commit(context, page_request(context), lambda: UUID(int=100))
    facts = facts_from_plan(plan)
    valid = success_outcome(facts)

    # When: each terminal fact is altered independently after persistence.
    tampered_commit = replace(
        facts.commits[-1], is_terminal_page=False, terminal_reason=None
    )
    mutations = (
        (facts, valid.model_copy(update={"final_cursor": "tampered"})),
        (facts, valid.model_copy(update={"final_page_ordinal": 99})),
        (replace(facts, commits=(tampered_commit,)), valid),
        (facts, valid.model_copy(update={"committed_page_hash_chain": "f" * 64})),
    )
    for tampered_facts, tampered in mutations:
        with pytest.raises(CollectionError) as captured:
            _ = prepare_completion(
                CompletionContext(
                    context.command,
                    (tampered_facts,),
                    NOW,
                ),
                CompletionRequest(
                    completion_idempotency_key=UUID(int=902),
                    attempt=1,
                    lease_token=LEASE,
                    source_outcomes=(tampered,),
                ),
            )
        assert captured.value.code is CollectionErrorCode.COMPLETION_MISMATCH

    # Then: the locked command and original run remain in their pre-final state.
    assert context.command.status.value == "running"
    assert context.run.status is RunStatus.RUNNING
    assert context.run.terminal_page_commit_id is None


def test_zero_data_page_success() -> None:
    # Given: ordinal zero is an empty source-exhausted terminal page.
    context = page_context()
    plan = prepare_page_commit(context, page_request(context), lambda: UUID(int=100))
    facts = facts_from_plan(plan)
    request = CompletionRequest(
        completion_idempotency_key=UUID(int=903),
        attempt=1,
        lease_token=LEASE,
        source_outcomes=(success_outcome(facts),),
    )

    # When: completion validates the persisted terminal marker.
    completion = prepare_completion(
        CompletionContext(context.command, (facts,), NOW), request
    )

    # Then: success has one auditable empty page and an empty post set.
    assert completion.command_status.value == "succeeded"
    assert completion.runs[0].terminal_page_ordinal == 0
    assert completion.publications[0].zero_post is True
    assert completion.publications[0].post_set_hash == canonical_json_hash([])


def test_zero_post_duplicate_and_rejection_semantics() -> None:
    # Given: three terminal pages representing empty, duplicate, and oversize-only.
    scenario = build_zero_post_scenario()

    # When: each persisted item outcome is finalized from server-owned facts.
    empty_completion = scenario.empty_completion
    duplicate_completion = scenario.duplicate_completion
    rejection_completion = scenario.rejection_completion

    # Then: only a persisted version makes zero_post false; empty hashes are [] hash.
    assert empty_completion.publications[0].zero_post is True
    assert scenario.duplicate_plan.outcome.response.duplicate_count == 1
    assert scenario.duplicate_plan.outcome.response.accepted_count == 0
    assert duplicate_completion.publications[0].zero_post is False
    assert duplicate_completion.publications[0].distinct_post_version_count == 1
    assert scenario.rejection_plan.outcome.response.rejected_count == 1
    assert rejection_completion.publications[0].zero_post is True
    assert rejection_completion.publications[0].post_set_hash == canonical_json_hash([])


def test_page_idempotent_response_loss() -> None:
    # Given: a first page plan that represents the durable commit before response loss.
    context = page_context()
    request = page_request(context)
    first = prepare_page_commit(context, request, lambda: UUID(int=110))
    replay_context = replace(
        context,
        checkpoint=first.updated_checkpoint,
        run=first.updated_run,
        existing_idempotency_commit=first.commit,
    )

    # When: the same request is planned from the persisted idempotency row.
    replay = prepare_page_commit(replay_context, request, lambda: UUID(int=700))

    # Then: replay is byte-stable, non-writing, and advances the cursor only once.
    assert first.outcome.status_code == 201
    assert replay.outcome.status_code == 200
    assert replay.outcome.response_bytes == first.outcome.response_bytes
    assert replay.should_persist is False
    assert first.updated_checkpoint.revision == 1
    assert replay.updated_checkpoint.revision == 1
    assert replay.updated_checkpoint.cursor == first.updated_checkpoint.cursor


def test_idempotency_payload_mismatch() -> None:
    # Given: one committed page and a new payload reusing its idempotency key.
    context = page_context()
    request = page_request(context)
    first = prepare_page_commit(context, request, lambda: UUID(int=120))
    replay_context = replace(context, existing_idempotency_commit=first.commit)
    changed = request.model_copy(
        update={"posts": (accepted_post(published_at=NOW.replace(minute=1)),)}
    )

    # When: the server compares the canonical semantic request hash.
    with pytest.raises(CollectionError) as captured:
        _ = prepare_page_commit(replay_context, changed, lambda: UUID(int=800))

    # Then: the exact conflict leaves the original commit and checkpoint untouched.
    assert captured.value.code is CollectionErrorCode.IDEMPOTENCY_KEY_REUSED
    assert replay_context.checkpoint.revision == 0
    assert replay_context.run.committed_page_count == 0


def test_cas_ordinal_lease_and_sealed_conflicts() -> None:
    # Given: contexts representing every server-side page conflict boundary.
    conflicts = build_page_conflict_cases()

    # When: each conflicting request reaches the planner.
    for case in conflicts:
        request = case.request
        context = case.context
        expected = case.code
        before_revision = context.checkpoint.revision
        before_page_count = context.run.committed_page_count
        with pytest.raises(CollectionError) as captured:
            _ = prepare_page_commit(context, request, lambda: UUID(int=900))

        # Then: status is exact and no immutable fixture fact changes.
        assert captured.value.code is expected
        assert context.checkpoint.revision == before_revision
        assert context.run.committed_page_count == before_page_count


def test_revocation_and_cap_terminal_reason() -> None:
    # Given: an authorization revoked before a new page and a premature cap marker.
    revoked = page_context(
        PageContextOverrides(
            authorization=replace(
                page_context().authorization,
                enabled=False,
                status=AuthorizationStatus.REVOKED,
            )
        )
    )
    premature_cap = page_request(
        page_context(),
        PageRequestOverrides(terminal_reason=TerminalReason.REVIEWED_PAGE_CAP),
    )

    # When/Then: source revocation is forbidden and cap claims fail closed as 422.
    with pytest.raises(CollectionError) as revoked_error:
        _ = prepare_page_commit(revoked, page_request(revoked), lambda: UUID(int=130))
    assert revoked_error.value.code is CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE
    assert revoked_error.value.status_code == 403
    with pytest.raises(CollectionError) as cap_error:
        _ = prepare_page_commit(page_context(), premature_cap, lambda: UUID(int=1000))
    assert cap_error.value.code is CollectionErrorCode.INVALID_TERMINAL_REASON
    assert cap_error.value.status_code == 422
