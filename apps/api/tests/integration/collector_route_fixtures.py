from __future__ import annotations

from typing import TYPE_CHECKING, Never, override

from app.api.routes.collector import (
    CommandResponse,
    create_collector_router,
)
from app.api.routes.collector_models import SkipDecisionResponse
from app.collection.base import CollectionError, CollectionErrorCode
from app.collection.page_commit import PageCommitOutcome, PageCommitResponse
from app.collection.repository import CollectionRepository
from app.collection.skip_decision_models import SkipDecisionOutcome
from app.core.errors import IdentityError, IdentityErrorCode, install_error_handlers
from app.core.principals import PrincipalId, Scope
from app.domain.enums import CommandStatus, RunStatus
from app.services.dashboard.models import AuthorizedService
from fastapi import FastAPI
from tests.integration.collector_test_constants import (
    COMMAND_ID,
    COMMIT_ID,
    IDEMPOTENCY_KEY,
    NOW,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.collection.command_store import (
        ClaimOperation,
        ConfirmOperation,
        ReserveOperation,
    )
    from app.collection.completion_store import CompletionOperation
    from app.collection.dispatch import ClaimCredentials
    from app.collection.page_service_models import PageCommitOperation
    from app.collection.skip_decision_models import SkipDecisionOperation
    from app.collection.slot_store import MaterializationOperation
    from app.services.dashboard.ports import ScopeAuthorizer
    from pydantic import SecretStr


class Authorizer:
    calls: list[Scope]

    def __init__(self) -> None:
        self.calls = []

    async def authorize(
        self, token: SecretStr, required_scope: Scope
    ) -> AuthorizedService:
        if token.get_secret_value() == "wrong-scope":
            raise IdentityError(
                IdentityErrorCode.INVALID_OIDC_CLAIMS,
                "service principal is not authorized",
            )
        self.calls.append(required_scope)
        return AuthorizedService(
            principal_id=PrincipalId("github:collector"),
            scopes=frozenset({required_scope}),
        )


class PageRepository(CollectionRepository):
    calls: int
    skip_calls: int
    conflict: bool

    def __init__(self, *, conflict: bool = False) -> None:
        self.calls = 0
        self.skip_calls = 0
        self.conflict = conflict

    @override
    async def commit_page(self, operation: PageCommitOperation) -> PageCommitOutcome:
        del operation
        if self.conflict:
            raise CollectionError(
                CollectionErrorCode.CHECKPOINT_CONFLICT,
                409,
                current_checkpoint_revision=8,
                current_cursor="cursor-current",
            )
        self.calls += 1
        response = PageCommitResponse(
            page_commit_id=COMMIT_ID,
            checkpoint_revision=8,
            next_cursor=None,
            accepted_count=0,
            duplicate_count=0,
            rejected_count=0,
            page_content_hash="a" * 64,
        )
        return PageCommitOutcome(
            status_code=201 if self.calls == 1 else 200,
            response=response,
            response_bytes=response.model_dump_json().encode(),
        )

    @override
    async def materialize(self, operation: MaterializationOperation) -> Never:
        del operation
        raise AssertionError

    @override
    async def reserve(self, operation: ReserveOperation) -> Never:
        del operation
        raise AssertionError

    @override
    async def confirm(self, operation: ConfirmOperation) -> Never:
        del operation
        raise AssertionError

    @override
    async def claim(self, operation: ClaimOperation) -> Never:
        del operation
        raise AssertionError

    @override
    async def checkpoint(self, run_id: UUID) -> Never:
        del run_id
        raise AssertionError

    @override
    async def heartbeat(self, command_id: UUID, credentials: ClaimCredentials) -> Never:
        del command_id, credentials
        raise AssertionError

    @override
    async def complete(self, operation: CompletionOperation) -> Never:
        del operation
        raise AssertionError

    @override
    async def attach_skip_decision(
        self, operation: SkipDecisionOperation
    ) -> SkipDecisionOutcome:
        self.skip_calls += 1
        response = SkipDecisionResponse(
            skip_decision_id=operation.payload.idempotency_key,
            terminal_status=RunStatus.SKIPPED_POLICY,
            evidence_sha256="d" * 64,
        )
        return SkipDecisionOutcome(201, response, response.model_dump_json().encode())


def app_for(authorizer: ScopeAuthorizer, repository: PageRepository) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_collector_router(authorizer, repository))
    return app


def page_body() -> dict[str, str | int | bool | None | list[None]]:
    return {
        "command_id": str(COMMAND_ID),
        "attempt": 1,
        "lease_token": "l" * 43,
        "page_idempotency_key": str(IDEMPOTENCY_KEY),
        "expected_checkpoint_revision": 7,
        "expected_cursor": "cursor-before",
        "next_cursor": None,
        "page_ordinal": 0,
        "posts": [],
        "source_page_item_count": 0,
        "source_page_receipt_sha256": "b" * 64,
        "page_fetch_started_at": NOW.isoformat(),
        "page_fetch_finished_at": NOW.isoformat(),
        "is_terminal_page": True,
        "terminal_reason": "source_exhausted",
    }


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
