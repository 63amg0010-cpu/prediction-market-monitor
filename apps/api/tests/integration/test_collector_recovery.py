from __future__ import annotations

import pytest
from app.api.routes.collector import CheckpointResponse
from app.collection.adapters.models import (
    AdapterPage,
    PageTermination,
    PreflightReady,
    RateLimitSnapshot,
)
from app.collection.base import CollectionError, CollectionErrorCode
from app.collection.cli import execute_collect_command
from app.collection.collector_workflow import (
    CommandSecrets,
    PageCursor,
    SourceExecution,
)
from app.collection.page_commit import page_chain_link
from app.domain.enums import SourcePlatform
from tests.integration.collector_test_constants import (
    COMMIT_ID,
    IDEMPOTENCY_KEY,
    NOW,
    SOURCE_ID,
)
from tests.integration.collector_workflow_fixtures import ConflictWorkflowControl


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("conflict_code", "recovered", "expected_ordinals"),
    [
        (
            CollectionErrorCode.CHECKPOINT_CONFLICT,
            CheckpointResponse(
                expected_checkpoint_revision=4,
                expected_cursor="server-cursor",
                next_page_ordinal=0,
                committed_page_hash_chain="0" * 64,
            ),
            [0, 0],
        ),
        (
            CollectionErrorCode.ORDINAL_GAP,
            CheckpointResponse(
                expected_checkpoint_revision=1,
                expected_cursor="cursor-0",
                next_page_ordinal=1,
                committed_page_hash_chain=page_chain_link("0" * 64, "1" * 64),
            ),
            [0, 1],
        ),
    ],
)
async def test_collect_command_recovers_from_authoritative_page_conflict(
    conflict_code: CollectionErrorCode,
    recovered: CheckpointResponse,
    expected_ordinals: list[int],
) -> None:
    # Given: the first page POST loses a CAS/ordinal race on the control plane.
    events: list[str] = []
    control = ConflictWorkflowControl(events, conflict_code, recovered)
    fetched: list[PageCursor] = []

    async def fetch_page(state: PageCursor) -> AdapterPage:
        fetched.append(state)
        events.append(f"fetch:{state.ordinal}:{state.cursor}")
        return AdapterPage(
            items=(),
            next_cursor=None,
            accepted_count=0,
            rejected_count=0,
            rate_limit=RateLimitSnapshot(
                used=None,
                remaining=None,
                reset_after_seconds=None,
                retry_after_seconds=None,
            ),
            termination=PageTermination.SOURCE_EXHAUSTED,
        )

    source = SourceExecution(
        source_id=SOURCE_ID,
        platform=SourcePlatform.REDDIT,
        preflight=lambda: PreflightReady(decision_id=COMMIT_ID),
        fetch_page=fetch_page,
    )

    # When: the public CLI workflow handles the typed HTTP conflict.
    await execute_collect_command(
        {
            "MONITOR_SCOPE_VERSION": "scope-v1",
            "MONITOR_DEPLOYMENT_ACTIVATION_AT": NOW.isoformat(),
            "MONITOR_SOURCE_IDS": str(SOURCE_ID),
            "GITHUB_RUN_ID": "9988",
            "GITHUB_RUN_ATTEMPT": "2",
        },
        control,
        (source,),
        lambda: CommandSecrets("n" * 43, "l" * 43, IDEMPOTENCY_KEY),
        lambda: NOW,
    )

    # Then: it reloads the server checkpoint and refetches from that exact state.
    assert [state.ordinal for state in fetched] == expected_ordinals
    assert fetched[-1].revision == recovered.expected_checkpoint_revision
    assert fetched[-1].cursor == recovered.expected_cursor
    assert control.checkpoint_calls == 2
    assert control.completion is not None
    outcome = control.completion.source_outcomes[0]
    assert outcome.committed_page_count == recovered.next_page_ordinal + 1
    assert events.index("checkpoint:recovered") < len(events) - 1


@pytest.mark.asyncio
async def test_collect_command_reloads_then_rejects_non_replay_conflict() -> None:
    # Given: the server rejects a changed payload under a consumed idempotency key.
    events: list[str] = []
    recovered = CheckpointResponse(
        expected_checkpoint_revision=0,
        expected_cursor=None,
        next_page_ordinal=0,
        committed_page_hash_chain="0" * 64,
    )
    control = ConflictWorkflowControl(
        events,
        CollectionErrorCode.IDEMPOTENCY_KEY_REUSED,
        recovered,
    )

    async def fetch_page(state: PageCursor) -> AdapterPage:
        events.append(f"fetch:{state.ordinal}")
        return AdapterPage(
            items=(),
            next_cursor=None,
            accepted_count=0,
            rejected_count=0,
            rate_limit=RateLimitSnapshot(
                used=None,
                remaining=None,
                reset_after_seconds=None,
                retry_after_seconds=None,
            ),
            termination=PageTermination.SOURCE_EXHAUSTED,
        )

    source = SourceExecution(
        source_id=SOURCE_ID,
        platform=SourcePlatform.REDDIT,
        preflight=lambda: PreflightReady(decision_id=COMMIT_ID),
        fetch_page=fetch_page,
    )

    # When: the public workflow receives a non-replayable 409.
    with pytest.raises(CollectionError) as raised:
        await execute_collect_command(
            {
                "MONITOR_SCOPE_VERSION": "scope-v1",
                "MONITOR_DEPLOYMENT_ACTIVATION_AT": NOW.isoformat(),
                "MONITOR_SOURCE_IDS": str(SOURCE_ID),
                "GITHUB_RUN_ID": "9988",
                "GITHUB_RUN_ATTEMPT": "2",
            },
            control,
            (source,),
            lambda: CommandSecrets("n" * 43, "l" * 43, IDEMPOTENCY_KEY),
            lambda: NOW,
        )

    # Then: it still reloads authoritative state, but never guesses a retry.
    assert raised.value.code is CollectionErrorCode.IDEMPOTENCY_KEY_REUSED
    assert control.checkpoint_calls == 2
    assert control.completion is None
    assert sum(event.startswith("fetch:") for event in events) == 1
