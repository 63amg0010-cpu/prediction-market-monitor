"""CSRF-authorized administrator command routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar, Protocol
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from fastapi import APIRouter, Depends, Header, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.errors import IdentityError, IdentityErrorCode
from app.services.dashboard.security import bearer_token


@dataclass(frozen=True, slots=True)
class AdminCommandContext:
    """BFF, session, same-origin, and CSRF evidence for one mutation."""

    bff_token: SecretStr
    session_token: SecretStr
    csrf_token: str
    origin: str | None
    referer: str | None


@dataclass(frozen=True, slots=True)
class CollectionRetryCommand:
    """Idempotent operator request to retry selected collection sources."""

    request_id: UUID
    source_ids: tuple[UUID, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class DailyReconcileCommand:
    """Idempotent operator request for bounded daily reconciliation."""

    request_id: UUID


class _CollectionRetryPayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID
    source_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=1, max_length=120)


class _DailyReconcilePayload(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    request_id: UUID


@dataclass(frozen=True, slots=True)
class _AdminHeaders:
    authorization: str | None
    session_token: str | None
    csrf_token: str | None
    origin: str | None
    referer: str | None


def _admin_headers(
    authorization: Annotated[str | None, Header()] = None,
    session_token: Annotated[str | None, Header(alias="X-Admin-Session")] = None,
    csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    origin: Annotated[str | None, Header()] = None,
    referer: Annotated[str | None, Header()] = None,
) -> _AdminHeaders:
    return _AdminHeaders(
        authorization=authorization,
        session_token=session_token,
        csrf_token=csrf_token,
        origin=origin,
        referer=referer,
    )


class CommandAccepted(BaseModel):
    """Created or idempotently recovered durable command identity."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    command_id: UUID
    created: bool


class AdminMutationAuthorizer(Protocol):
    """Validate BFF admin scope, session state, origin, and CSRF together."""

    async def authorize(self, context: AdminCommandContext) -> None:
        """Reject unless every administrator mutation guard passes."""
        ...


class AdminCommandHandler(Protocol):
    """Persist retry and daily reconciliation commands idempotently."""

    async def retry_collection(
        self, command: CollectionRetryCommand
    ) -> CommandAccepted:
        """Create or recover a collection retry command."""
        ...

    async def reconcile_daily(self, command: DailyReconcileCommand) -> CommandAccepted:
        """Create or recover a bounded reconciliation command."""
        ...


def create_commands_router(
    authorizer: AdminMutationAuthorizer, handler: AdminCommandHandler
) -> APIRouter:
    """Create fail-closed administrator control routes."""
    router = APIRouter(tags=["commands"])

    async def authorize(
        headers: _AdminHeaders,
    ) -> None:
        if headers.session_token is None or headers.csrf_token is None:
            raise IdentityError(
                IdentityErrorCode.INVALID_TOKEN,
                "administrator session and CSRF token required",
            )
        await authorizer.authorize(
            AdminCommandContext(
                bff_token=bearer_token(headers.authorization),
                session_token=SecretStr(headers.session_token),
                csrf_token=headers.csrf_token,
                origin=headers.origin,
                referer=headers.referer,
            )
        )

    @router.post("/v1/commands/collection-retry", response_model=CommandAccepted)
    async def retry_collection(
        payload: _CollectionRetryPayload,
        response: Response,
        headers: Annotated[_AdminHeaders, Depends(_admin_headers)],
    ) -> CommandAccepted:
        """Queue an idempotent source-scoped collection retry."""
        await authorize(headers)
        result = await handler.retry_collection(
            CollectionRetryCommand(
                request_id=payload.request_id,
                source_ids=payload.source_ids,
                reason=payload.reason,
            )
        )
        response.status_code = (
            status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
        )
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.post("/v1/admin/daily-reconcile", response_model=CommandAccepted)
    async def reconcile_daily(
        payload: _DailyReconcilePayload,
        response: Response,
        headers: Annotated[_AdminHeaders, Depends(_admin_headers)],
    ) -> CommandAccepted:
        """Queue bounded report and retention reconciliation."""
        await authorize(headers)
        result = await handler.reconcile_daily(
            DailyReconcileCommand(request_id=payload.request_id)
        )
        response.status_code = (
            status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
        )
        response.headers["Cache-Control"] = "no-store"
        return result

    _ = retry_collection, reconcile_daily
    return router
