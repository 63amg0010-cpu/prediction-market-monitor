from __future__ import annotations

from uuid import UUID

import pytest
from app.collection.adapters.models import (
    AdapterPage,
    BlockedKind,
    PageTermination,
    PreflightBlocked,
    PreflightReady,
    RateLimitSnapshot,
    SourceBlockedError,
)
from app.collection.cli import execute_collect_command
from app.collection.collector_workflow import (
    CollectorControlPlane,
    CommandSecrets,
    PageCursor,
    SourceExecution,
)
from app.collection.page_commit import page_chain_link
from app.domain.enums import (
    BudgetDecisionStatus,
    CommandStatus,
    RunStatus,
    SourcePlatform,
)
from tests.integration.collector_test_constants import (
    COMMIT_ID,
    IDEMPOTENCY_KEY,
    NOW,
    SOURCE_ID,
)
from tests.integration.collector_workflow_fixtures import (
    WorkflowControl,
    claimed_run_response,
)


@pytest.mark.asyncio
async def test_collect_command_executes_the_full_control_plane_in_order() -> None:
    # Given: one claimed source whose authorized adapter returns two pages.
    events: list[str] = []
    workflow_control = WorkflowControl(events)
    control: CollectorControlPlane = workflow_control

    def preflight() -> PreflightReady:
        events.append("preflight")
        return PreflightReady(decision_id=COMMIT_ID)

    async def fetch_page(state: PageCursor) -> AdapterPage:
        events.append(f"fetch:{state.ordinal}")
        termination = (
            PageTermination.CONTINUE
            if state.ordinal == 0
            else PageTermination.SOURCE_EXHAUSTED
        )
        return AdapterPage(
            items=(),
            next_cursor="cursor-0" if state.ordinal == 0 else None,
            accepted_count=0,
            rejected_count=0,
            rate_limit=RateLimitSnapshot(
                used=None,
                remaining=None,
                reset_after_seconds=None,
                retry_after_seconds=None,
            ),
            termination=termination,
        )

    source = SourceExecution(
        source_id=SOURCE_ID,
        platform=SourcePlatform.REDDIT,
        preflight=preflight,
        fetch_page=fetch_page,
    )
    environment = {
        "MONITOR_SCOPE_VERSION": "scope-v1",
        "MONITOR_DEPLOYMENT_ACTIVATION_AT": NOW.isoformat(),
        "MONITOR_SOURCE_IDS": str(SOURCE_ID),
        "GITHUB_RUN_ID": "9988",
        "GITHUB_RUN_ATTEMPT": "2",
    }

    # When: the same collect command used by the CLI entrypoint executes.
    completions = await execute_collect_command(
        environment,
        control,
        (source,),
        lambda: CommandSecrets("n" * 43, "l" * 43, IDEMPOTENCY_KEY),
        lambda: NOW,
    )

    # Then: each fetch is followed by its page commit before another fetch.
    assert events == [
        "oidc",
        "preflight",
        "materialize",
        "reserve",
        "confirm",
        "claim",
        "checkpoint",
        "heartbeat",
        "fetch:0",
        "page:0:False",
        "heartbeat",
        "fetch:1",
        "page:1:True",
        "heartbeat",
        "complete",
    ]
    assert [request.is_terminal_page for request in workflow_control.page_requests] == [
        False,
        True,
    ]
    assert workflow_control.completion is not None
    assert len(completions) == 1
    assert completions[0].status is CommandStatus.SUCCEEDED
    assert completions[0].completed_at == NOW
    outcome = workflow_control.completion.source_outcomes[0]
    assert outcome.terminal_status is RunStatus.SUCCEEDED
    assert outcome.last_page_commit_id == UUID(int=2)
    assert outcome.committed_page_count == 2
    assert outcome.committed_page_hash_chain == page_chain_link(
        page_chain_link("0" * 64, "1" * 64), "2" * 64
    )


@pytest.mark.asyncio
async def test_collect_command_blocks_source_before_materialization() -> None:
    # Given: a configured source whose adapter preflight is blocked.
    events: list[str] = []
    control: CollectorControlPlane = WorkflowControl(events)

    def blocked() -> PreflightBlocked:
        events.append("preflight:blocked")
        return PreflightBlocked(
            kind=BlockedKind.BLOCKED_AUTHORIZATION,
            code="authorization_missing",
        )

    async def forbidden_fetch(state: PageCursor) -> AdapterPage:
        del state
        raise AssertionError

    source = SourceExecution(
        source_id=SOURCE_ID,
        platform=SourcePlatform.REDDIT,
        preflight=blocked,
        fetch_page=forbidden_fetch,
    )
    environment = {
        "MONITOR_SCOPE_VERSION": "scope-v1",
        "MONITOR_DEPLOYMENT_ACTIVATION_AT": NOW.isoformat(),
        "MONITOR_SOURCE_IDS": str(SOURCE_ID),
        "GITHUB_RUN_ID": "9988",
        "GITHUB_RUN_ATTEMPT": "2",
    }

    # When: the actual collect command evaluates the source gate.
    with pytest.raises(SourceBlockedError):
        _ = await execute_collect_command(
            environment,
            control,
            (source,),
            lambda: CommandSecrets("n" * 43, "l" * 43, IDEMPOTENCY_KEY),
            lambda: NOW,
        )

    # Then: no command mutation, completion, or provider request is attempted.
    assert events == ["oidc", "preflight:blocked"]


@pytest.mark.asyncio
async def test_hard_stop_claim_completes_without_provider_fetch() -> None:
    # Given: the claim persisted an 80-percent hard stop and its quota proof.
    events: list[str] = []
    skip_decision_id = UUID(int=902)
    workflow_control = WorkflowControl(
        events,
        claimed_run=claimed_run_response(
            budget_status=BudgetDecisionStatus.HARD_STOP,
            reviewed_page_cap=0,
            reviewed_post_cap=0,
            skip_decision_id=skip_decision_id,
            precomputed_terminal_status=RunStatus.SKIPPED_QUOTA,
        ),
    )

    def preflight() -> PreflightReady:
        events.append("preflight")
        return PreflightReady(decision_id=COMMIT_ID)

    async def forbidden_fetch(state: PageCursor) -> AdapterPage:
        del state
        raise AssertionError

    source = SourceExecution(
        source_id=SOURCE_ID,
        platform=SourcePlatform.REDDIT,
        preflight=preflight,
        fetch_page=forbidden_fetch,
    )

    # When: the production collector executes that precomputed terminal run.
    completions = await execute_collect_command(
        {
            "MONITOR_SCOPE_VERSION": "scope-v1",
            "MONITOR_DEPLOYMENT_ACTIVATION_AT": NOW.isoformat(),
            "MONITOR_SOURCE_IDS": str(SOURCE_ID),
            "GITHUB_RUN_ID": "9988",
            "GITHUB_RUN_ATTEMPT": "2",
        },
        workflow_control,
        (source,),
        lambda: CommandSecrets("n" * 43, "l" * 43, IDEMPOTENCY_KEY),
        lambda: NOW,
    )

    # Then: completion attaches the proof without checkpoint, heartbeat, or fetch.
    assert events == [
        "oidc",
        "preflight",
        "materialize",
        "reserve",
        "confirm",
        "claim",
        "complete",
    ]
    assert workflow_control.completion is not None
    assert len(completions) == 1
    assert completions[0].status is CommandStatus.SUCCEEDED
    assert completions[0].completed_at == NOW
    outcome = workflow_control.completion.source_outcomes[0]
    assert outcome.terminal_status is RunStatus.SKIPPED_QUOTA
    assert outcome.skip_decision_id == skip_decision_id
    assert outcome.committed_page_count == 0
