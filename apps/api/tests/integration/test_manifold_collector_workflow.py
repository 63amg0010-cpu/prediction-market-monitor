from __future__ import annotations

from typing import override
from uuid import UUID

import pytest
from app.api.routes.collector import (
    CheckpointResponse,
    ClaimedRunResponse,
    ClaimResponse,
)
from app.collection.adapters.http_errors import (
    AdapterHttpError,
    HttpFailure,
    classify_http_failure,
)
from app.collection.adapters.models import (
    AdapterPage,
    PageTermination,
    PreflightReady,
    RateLimitSnapshot,
)
from app.collection.cli import execute_collect_command
from app.collection.collector_workflow import (
    CommandSecrets,
    PageCursor,
    SourceExecution,
)
from app.collection.page_commit import PageCommitRequest, PageCommitResponse
from app.domain.enums import CommandStatus, RunStatus, SourcePlatform
from tests.integration.collector_test_constants import COMMIT_ID, IDEMPOTENCY_KEY, NOW
from tests.integration.collector_workflow_fixtures import (
    WorkflowControl,
    claimed_run_response,
    command_response,
)


class _MixedSourceWorkflowControl(WorkflowControl):
    def __init__(
        self,
        events: list[str],
        source_ids: tuple[UUID, UUID],
        run_ids: tuple[UUID, UUID],
    ) -> None:
        super().__init__(events)
        self.source_ids: tuple[UUID, UUID] = source_ids
        self.run_ids: tuple[UUID, UUID] = run_ids
        base_run = claimed_run_response(reviewed_page_cap=1)
        self.runs: tuple[ClaimedRunResponse, ClaimedRunResponse] = (
            base_run.model_copy(
                update={"run_id": run_ids[0], "source_id": source_ids[0]}
            ),
            base_run.model_copy(
                update={"run_id": run_ids[1], "source_id": source_ids[1]}
            ),
        )

    @override
    async def claim(
        self,
        command_id: UUID,
        attempt: int,
        lease_token: str,
        reservation_nonce: str,
        source_ids: tuple[UUID, ...],
    ) -> ClaimResponse:
        del command_id, attempt, lease_token, reservation_nonce
        assert source_ids == self.source_ids
        self.events.append("claim")
        return ClaimResponse(
            command=command_response(CommandStatus.RUNNING),
            runs=self.runs,
        )

    @override
    async def checkpoint(self, run_id: UUID) -> CheckpointResponse:
        assert run_id in self.run_ids
        self.events.append(f"checkpoint:{run_id.int}")
        return CheckpointResponse(
            expected_checkpoint_revision=0,
            expected_cursor=None,
            next_page_ordinal=0,
            committed_page_hash_chain="0" * 64,
        )

    @override
    async def commit_page(
        self,
        run_id: UUID,
        request: PageCommitRequest,
    ) -> PageCommitResponse:
        assert run_id == self.run_ids[1]
        self.page_requests.append(request)
        self.events.append("dcinside:page")
        return PageCommitResponse(
            page_commit_id=UUID(int=701),
            checkpoint_revision=1,
            next_cursor=None,
            accepted_count=0,
            duplicate_count=0,
            rejected_count=0,
            page_content_hash="7" * 64,
        )


@pytest.mark.asyncio
async def test_manifold_provider_failure_does_not_abort_dcinside_commit() -> None:
    source_ids = (UUID(int=601), UUID(int=602))
    run_ids = (UUID(int=501), UUID(int=502))
    events: list[str] = []
    control = _MixedSourceWorkflowControl(events, source_ids, run_ids)

    def ready() -> PreflightReady:
        return PreflightReady(decision_id=COMMIT_ID)

    async def manifold_failure(_: PageCursor) -> AdapterPage:
        failure = classify_http_failure(HttpFailure(status_code=503))
        raise AdapterHttpError(failure, 503, "/v0/comments")

    async def dcinside_success(_: PageCursor) -> AdapterPage:
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

    sources = (
        SourceExecution(
            source_id=source_ids[0],
            platform=SourcePlatform.MANIFOLD,
            preflight=ready,
            fetch_page=manifold_failure,
        ),
        SourceExecution(
            source_id=source_ids[1],
            platform=SourcePlatform.DCINSIDE,
            preflight=ready,
            fetch_page=dcinside_success,
        ),
    )
    completions = await execute_collect_command(
        {
            "MONITOR_SCOPE_VERSION": "scope-v1",
            "MONITOR_DEPLOYMENT_ACTIVATION_AT": NOW.isoformat(),
            "MONITOR_SOURCE_IDS": f"{source_ids[0]},{source_ids[1]}",
            "GITHUB_RUN_ID": "9988",
            "GITHUB_RUN_ATTEMPT": "2",
        },
        control,
        sources,
        lambda: CommandSecrets("n" * 43, "l" * 43, IDEMPOTENCY_KEY),
        lambda: NOW,
    )

    assert "dcinside:page" in events
    assert control.completion is not None
    assert len(completions) == 1
    assert completions[0].status is CommandStatus.SUCCEEDED
    assert completions[0].completed_at == NOW
    outcomes = {
        outcome.run_id: outcome for outcome in control.completion.source_outcomes
    }
    assert outcomes[run_ids[0]].terminal_status is RunStatus.FAILED_RETRYABLE
    failure_detail = outcomes[run_ids[0]].failure
    assert failure_detail is not None
    assert failure_detail.code == "provider_temporarily_unavailable"
    assert outcomes[run_ids[1]].terminal_status is RunStatus.SUCCEEDED
    assert outcomes[run_ids[1]].committed_page_count == 1
