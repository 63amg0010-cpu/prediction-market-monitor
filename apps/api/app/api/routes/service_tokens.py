"""Server-only service-token exchange routes."""

from __future__ import annotations

from typing import Annotated, ClassVar
from uuid import UUID  # noqa: TC003 - Pydantic resolves this field at runtime.

from fastapi import APIRouter, Header, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.errors import IdentityError, IdentityErrorCode, correlation_id_from_header
from app.core.principals import (  # noqa: TC001 - Pydantic runtime fields.
    CredentialVersion,
    Scope,
)
from app.services.identity.exchanges import (
    BffExchangeCommand,
    BffExchangeResponse,
    GitHubExchangeCommand,
    ServiceTokenExchangeHandler,
    WorkerExchangeCommand,
)
from app.services.identity.windows import (  # noqa: TC001 - FastAPI body model.
    WorkerBootstrapRequest,
)


class BffExchangePayload(BaseModel):
    """Validated BFF exchange JSON body."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    credential_version: CredentialVersion = Field(min_length=1)
    request_nonce: UUID
    requested_scopes: frozenset[Scope] = Field(min_length=1)


class GitHubExchangePayload(BaseModel):
    """GitHub OIDC exchange JSON body."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    oidc_token: SecretStr


def _bearer_secret(authorization: str) -> SecretStr:
    if not authorization.startswith("Bearer "):
        raise IdentityError(
            IdentityErrorCode.INVALID_CREDENTIAL, "Bearer credential required"
        )
    value = authorization.removeprefix("Bearer ")
    if not value:
        raise IdentityError(
            IdentityErrorCode.INVALID_CREDENTIAL, "Bearer credential required"
        )
    return SecretStr(value)


def _set_private_response_headers(
    response: Response, correlation_id: str | None
) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Correlation-ID"] = correlation_id_from_header(correlation_id)


def create_service_token_router(
    handler: ServiceTokenExchangeHandler,
) -> APIRouter:
    """Create service-token routes around an injected fail-closed handler."""
    router = APIRouter(prefix="/v1/service-tokens", tags=["service-tokens"])

    @router.post(
        "/bff/exchange",
        response_model=BffExchangeResponse,
        status_code=status.HTTP_200_OK,
    )
    async def exchange_bff(
        payload: BffExchangePayload,
        response: Response,
        authorization: Annotated[str, Header()],
        deployment_identity: Annotated[str, Header(alias="X-Deployment-Identity")],
        correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    ) -> BffExchangeResponse:
        """Exchange the server-only BFF credential for a short-lived JWT."""
        result = await handler.exchange_bff(
            BffExchangeCommand(
                credential_version=payload.credential_version,
                presented_credential=_bearer_secret(authorization),
                request_nonce=str(payload.request_nonce),
                requested_scopes=payload.requested_scopes,
                deployment_identity=deployment_identity,
            )
        )
        _set_private_response_headers(response, correlation_id)
        return result

    @router.post(
        "/github/exchange",
        response_model=BffExchangeResponse,
        status_code=status.HTTP_200_OK,
    )
    async def exchange_github(
        payload: GitHubExchangePayload,
        response: Response,
        correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    ) -> BffExchangeResponse:
        """Exchange a cryptographically verified GitHub OIDC token."""
        result = await handler.exchange_github(
            GitHubExchangeCommand(oidc_token=payload.oidc_token)
        )
        _set_private_response_headers(response, correlation_id)
        return result

    @router.post(
        "/worker/exchange",
        response_model=BffExchangeResponse,
        status_code=status.HTTP_200_OK,
    )
    async def exchange_worker(
        payload: WorkerBootstrapRequest,
        response: Response,
        correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    ) -> BffExchangeResponse:
        """Exchange a signed Windows bootstrap request for a short-lived JWT."""
        result = await handler.exchange_worker(WorkerExchangeCommand(request=payload))
        _set_private_response_headers(response, correlation_id)
        return result

    _ = exchange_bff, exchange_github, exchange_worker
    return router
