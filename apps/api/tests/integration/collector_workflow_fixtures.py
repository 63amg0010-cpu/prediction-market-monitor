from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Never, override
from uuid import UUID

from app.api.routes.collector import (
    CheckpointResponse,
    ClaimedRunResponse,
    ClaimResponse,
    CommandResponse,
)
from app.collection.adapters.models import HttpMethod, SourceAuthorizationDecision
from app.collection.base import CollectionError, CollectionErrorCode
from app.collection.completion_models import CompletionRequest, CompletionResponse
from app.collection.page_commit import PageCommitRequest, PageCommitResponse
from app.domain.enums import (
    AuthorizationStatus,
    BudgetDecisionStatus,
    CommandStatus,
    RunStatus,
    SourcePlatform,
)
from tests.integration.collector_test_constants import (
    COMMAND_ID,
    COMMIT_ID,
    NOW,
    RUN_ID,
    SOURCE_ID,
)

if TYPE_CHECKING:
    from datetime import datetime

    from app.api.routes.collector_models import SkipDecisionPayload


class WorkflowControl:
    def __init__(
        self,
        events: list[str],
        *,
        claimed_run: ClaimedRunResponse | None = None,
    ) -> None:
        self.events: list[str] = events
        self.page_requests: list[PageCommitRequest] = []
        self.completion: CompletionRequest | None = None
        self.claimed_run: ClaimedRunResponse = claimed_run or claimed_run_response()

    async def authenticate(self) -> None:
        self.events.append("oidc")

    async def materialize(
        self, scope_version: str, deployment_activation_at: datetime
    ) -> tuple[UUID, ...]:
        assert scope_version == "scope-v1"
        assert deployment_activation_at == NOW
        self.events.append("materialize")
        return (COMMAND_ID,)

    async def reserve(
        self, command_id: UUID, reservation_nonce: str, lease_token: str
    ) -> CommandResponse:
        assert command_id == COMMAND_ID
        assert reservation_nonce == "n" * 43
        assert lease_token == "l" * 43
        self.events.append("reserve")
        return command_response(CommandStatus.DISPATCH_RESERVED)

    async def confirm(
        self,
        command_id: UUID,
        attempt: int,
        reservation_nonce: str,
        github_run_id: str,
        github_run_attempt: int,
    ) -> CommandResponse:
        assert (command_id, attempt, reservation_nonce) == (
            COMMAND_ID,
            1,
            "n" * 43,
        )
        assert (github_run_id, github_run_attempt) == ("9988", 2)
        self.events.append("confirm")
        return command_response(CommandStatus.DISPATCHED)

    async def claim(
        self,
        command_id: UUID,
        attempt: int,
        lease_token: str,
        reservation_nonce: str,
        source_ids: tuple[UUID, ...],
    ) -> ClaimResponse:
        assert (command_id, attempt, lease_token, reservation_nonce) == (
            COMMAND_ID,
            1,
            "l" * 43,
            "n" * 43,
        )
        assert source_ids == (SOURCE_ID,)
        self.events.append("claim")
        return ClaimResponse(
            command=command_response(CommandStatus.RUNNING),
            runs=(self.claimed_run,),
        )

    async def checkpoint(self, run_id: UUID) -> CheckpointResponse:
        assert run_id == RUN_ID
        self.events.append("checkpoint")
        return CheckpointResponse(
            expected_checkpoint_revision=0,
            expected_cursor=None,
            next_page_ordinal=0,
            committed_page_hash_chain="0" * 64,
        )

    async def commit_page(
        self, run_id: UUID, request: PageCommitRequest
    ) -> PageCommitResponse:
        assert run_id == RUN_ID
        self.page_requests.append(request)
        self.events.append(f"page:{request.page_ordinal}:{request.is_terminal_page}")
        return PageCommitResponse(
            page_commit_id=UUID(int=request.page_ordinal + 1),
            checkpoint_revision=request.expected_checkpoint_revision + 1,
            next_cursor=request.next_cursor,
            accepted_count=0,
            duplicate_count=0,
            rejected_count=0,
            page_content_hash=str(request.page_ordinal + 1) * 64,
        )

    async def heartbeat(
        self, command_id: UUID, attempt: int, lease_token: str
    ) -> CommandResponse:
        assert (command_id, attempt, lease_token) == (COMMAND_ID, 1, "l" * 43)
        self.events.append("heartbeat")
        return command_response(CommandStatus.RUNNING)

    async def attach_skip_decision(
        self, run_id: UUID, payload: SkipDecisionPayload
    ) -> Never:
        del run_id, payload
        raise AssertionError

    async def complete(
        self, command_id: UUID, request: CompletionRequest
    ) -> CompletionResponse:
        assert command_id == COMMAND_ID
        self.events.append("complete")
        self.completion = request
        return CompletionResponse(
            command_id=COMMAND_ID,
            status=CommandStatus.SUCCEEDED,
            completed_at=NOW,
            publications=(),
        )


class ConflictWorkflowControl(WorkflowControl):
    def __init__(
        self,
        events: list[str],
        conflict_code: CollectionErrorCode,
        recovered: CheckpointResponse,
    ) -> None:
        super().__init__(events)
        self.conflict_code: CollectionErrorCode = conflict_code
        self.recovered: CheckpointResponse = recovered
        self.checkpoint_calls: int = 0

    @override
    async def checkpoint(self, run_id: UUID) -> CheckpointResponse:
        self.checkpoint_calls += 1
        if self.checkpoint_calls == 1:
            return await super().checkpoint(run_id)
        assert run_id == RUN_ID
        self.events.append("checkpoint:recovered")
        return self.recovered

    @override
    async def commit_page(
        self, run_id: UUID, request: PageCommitRequest
    ) -> PageCommitResponse:
        if not self.page_requests:
            assert run_id == RUN_ID
            self.page_requests.append(request)
            self.events.append(f"page:{request.page_ordinal}:conflict")
            raise CollectionError(
                self.conflict_code,
                409,
                current_checkpoint_revision=self.recovered.expected_checkpoint_revision,
                current_cursor=self.recovered.expected_cursor,
                expected_page_ordinal=self.recovered.next_page_ordinal,
            )
        return await super().commit_page(run_id, request)


def command_response(status: CommandStatus) -> CommandResponse:
    return CommandResponse.model_validate(
        {
            "id": COMMAND_ID,
            "status": status,
            "attempt": 1,
            "available_at": NOW,
            "heartbeat_at": NOW if status is CommandStatus.RUNNING else None,
        }
    )


def claimed_run_response(
    *,
    budget_status: BudgetDecisionStatus = BudgetDecisionStatus.ALLOW,
    reviewed_page_cap: int = 4,
    reviewed_post_cap: int = 20,
    skip_decision_id: UUID | None = None,
    precomputed_terminal_status: RunStatus | None = None,
) -> ClaimedRunResponse:
    authorization = SourceAuthorizationDecision(
        decision_id=COMMIT_ID,
        source=SourcePlatform.REDDIT,
        status=AuthorizationStatus.APPROVED,
        evidence_sha256="a" * 64,
        evidence_location="https://evidence.example.test/reddit-approval",
        issuer="provider",
        reviewer="owner",
        permitted_methods=frozenset({HttpMethod.GET}),
        permitted_routes=frozenset({"/r/test/new"}),
        permitted_fields=frozenset({"title"}),
        permitted_subreddits=frozenset({"test"}),
        purpose="tests",
        requests_per_minute=30,
        concurrency=1,
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        revoked_at=None,
    )
    return ClaimedRunResponse.model_validate(
        {
            "id": RUN_ID,
            "source_id": SOURCE_ID,
            "scope_version": "scope-v1",
            "attempt": 1,
            "status": RunStatus.RUNNING,
            "start_checkpoint_revision": 0,
            "start_cursor": None,
            "next_page_ordinal": 0,
            "committed_page_count": 0,
            "committed_page_hash_chain": "0" * 64,
            "authorization_decision_id": authorization.decision_id,
            "authorization_snapshot": authorization,
            "budget_decision_id": UUID(int=1),
            "budget_decision_status": budget_status,
            "reviewed_page_cap": reviewed_page_cap,
            "reviewed_post_cap": reviewed_post_cap,
            "skip_decision_id": skip_decision_id,
            "precomputed_terminal_status": precomputed_terminal_status,
        }
    )
