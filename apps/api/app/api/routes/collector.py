"""Scoped HTTP boundary for the durable collection control plane."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from fastapi import APIRouter, Header, Response, status

from app.collection.command_store import (
    ClaimOperation,
    ConfirmOperation,
    ReserveOperation,
)
from app.collection.completion_models import (  # noqa: TC001 - Pydantic runtime field.
    CompletionRequest,
)
from app.collection.completion_store import CompletionOperation
from app.collection.dispatch import (
    ClaimCredentials,
    DispatchConfirmation,
    DispatchReservation,
)
from app.collection.page_commit import PageCommitRequest, PageCommitResponse
from app.collection.page_service_models import PageCommitOperation
from app.collection.skip_decision_models import SkipDecisionOperation
from app.collection.slot_store import MaterializationOperation
from app.core.principals import Scope
from app.services.dashboard.security import require_scope

from .collector_errors import collection_call
from .collector_models import (
    CheckpointResponse,
    ClaimedRunResponse,
    ClaimPayload,
    ClaimResponse,
    CommandResponse,
    ConfirmPayload,
    HeartbeatPayload,
    MaterializePayload,
    MaterializeResponse,
    ReservePayload,
    SkipDecisionPayload,
    SkipDecisionResponse,
)

if TYPE_CHECKING:
    from app.collection.commands import CommandState
    from app.collection.repository import CollectionRepository
    from app.services.dashboard.ports import ScopeAuthorizer


def _command_response(command: CommandState) -> CommandResponse:
    return CommandResponse.model_validate(command)


def create_collector_router(  # noqa: C901 - cohesive scoped route registration.
    authorizer: ScopeAuthorizer, handler: CollectionRepository
) -> APIRouter:
    """Create least-privilege collector routes around an atomic repository."""
    router = APIRouter(prefix="/v1/collector", tags=["collector"])

    async def authorize(header: str | None, scope: Scope) -> None:
        _ = await require_scope(authorizer, header, scope)

    @router.post("/materialize", response_model=MaterializeResponse)
    async def materialize(
        payload: MaterializePayload,
        authorization: Annotated[str | None, Header()] = None,
        correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    ) -> MaterializeResponse | Response:
        await authorize(authorization, Scope.COLLECTOR_MATERIALIZE)
        result = await collection_call(
            handler.materialize(
                MaterializationOperation(
                    payload.scope_version, payload.deployment_activation_at
                )
            ),
            correlation_id,
        )
        if isinstance(result, Response):
            return result
        return MaterializeResponse(command_ids=result)

    @router.post("/commands/{command_id}/reserve", response_model=CommandResponse)
    async def reserve(
        command_id: UUID,
        payload: ReservePayload,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CommandResponse | Response:
        await authorize(authorization, Scope.COLLECTOR_RESERVE)
        result = await collection_call(
            handler.reserve(
                ReserveOperation(
                    command_id,
                    DispatchReservation(payload.reservation_nonce, payload.lease_token),
                )
            )
        )
        return result if isinstance(result, Response) else _command_response(result)

    @router.post(
        "/commands/{command_id}/confirm-dispatch", response_model=CommandResponse
    )
    async def confirm(
        command_id: UUID,
        payload: ConfirmPayload,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CommandResponse | Response:
        await authorize(authorization, Scope.COLLECTOR_RESERVE)
        result = await collection_call(
            handler.confirm(
                ConfirmOperation(
                    command_id,
                    DispatchConfirmation(
                        payload.attempt,
                        payload.reservation_nonce,
                        payload.github_run_id,
                        payload.github_run_attempt,
                    ),
                )
            )
        )
        return result if isinstance(result, Response) else _command_response(result)

    @router.post("/commands/{command_id}/claim", response_model=ClaimResponse)
    async def claim(
        command_id: UUID,
        payload: ClaimPayload,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ClaimResponse | Response:
        await authorize(authorization, Scope.COLLECTOR_CLAIM)
        result = await collection_call(
            handler.claim(
                ClaimOperation(
                    command_id,
                    ClaimCredentials(
                        payload.attempt,
                        payload.lease_token,
                        payload.reservation_nonce,
                    ),
                    payload.source_ids,
                )
            )
        )
        if isinstance(result, Response):
            return result
        runs = tuple(ClaimedRunResponse.model_validate(run) for run in result.runs)
        return ClaimResponse(command=_command_response(result.command), runs=runs)

    @router.get("/runs/{run_id}/checkpoint", response_model=CheckpointResponse)
    async def checkpoint(
        run_id: UUID,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CheckpointResponse | Response:
        await authorize(authorization, Scope.COLLECTOR_PAGE_COMMIT)
        result = await collection_call(handler.checkpoint(run_id))
        return (
            result
            if isinstance(result, Response)
            else CheckpointResponse.model_validate(result)
        )

    @router.post(
        "/runs/{run_id}/pages",
        response_model=PageCommitResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def commit_page(
        run_id: UUID,
        payload: PageCommitRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        await authorize(authorization, Scope.COLLECTOR_PAGE_COMMIT)
        result = await collection_call(
            handler.commit_page(PageCommitOperation(run_id, payload))
        )
        if isinstance(result, Response):
            return result
        return Response(
            content=result.response_bytes,
            status_code=result.status_code,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @router.post("/commands/{command_id}/heartbeat", response_model=CommandResponse)
    async def heartbeat(
        command_id: UUID,
        payload: HeartbeatPayload,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CommandResponse | Response:
        await authorize(authorization, Scope.COLLECTOR_HEARTBEAT)
        result = await collection_call(
            handler.heartbeat(
                command_id,
                ClaimCredentials(payload.attempt, payload.lease_token, "unused"),
            )
        )
        return result if isinstance(result, Response) else _command_response(result)

    @router.post(
        "/commands/{command_id}/complete",
        response_model=None,
        status_code=status.HTTP_200_OK,
    )
    async def complete(
        command_id: UUID,
        payload: CompletionRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        await authorize(authorization, Scope.COLLECTOR_COMPLETE)
        result = await collection_call(
            handler.complete(CompletionOperation(command_id, payload))
        )
        if isinstance(result, Response):
            return result
        return Response(
            content=result.response_bytes,
            status_code=result.status_code,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    @router.post(
        "/runs/{run_id}/skip-decision",
        response_model=SkipDecisionResponse,
        status_code=status.HTTP_201_CREATED,
    )
    async def skip_decision(
        run_id: UUID,
        payload: SkipDecisionPayload,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        service = await require_scope(
            authorizer, authorization, Scope.COLLECTOR_COMPLETE
        )
        result = await collection_call(
            handler.attach_skip_decision(
                SkipDecisionOperation(run_id, payload, str(service.principal_id))
            )
        )
        if isinstance(result, Response):
            return result
        return Response(
            content=result.response_bytes,
            status_code=result.status_code,
            media_type="application/json",
            headers={"Cache-Control": "no-store"},
        )

    _ = (
        materialize,
        reserve,
        confirm,
        claim,
        checkpoint,
        commit_page,
        heartbeat,
        complete,
        skip_decision,
    )
    return router


__all__ = (
    "CheckpointResponse",
    "ClaimResponse",
    "ClaimedRunResponse",
    "CommandResponse",
    "MaterializeResponse",
    "create_collector_router",
)
