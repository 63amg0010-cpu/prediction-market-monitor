"""Single-administrator BFF session routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic resolves this at runtime.
from typing import Annotated, ClassVar, Protocol

from fastapi import APIRouter, Header, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.errors import (
    IdentityError,
    IdentityErrorCode,
    correlation_id_from_header,
)


@dataclass(frozen=True, slots=True)
class LoginCommand:
    """BFF-authenticated administrator login request."""

    bff_token: SecretStr
    password: SecretStr
    client_ip: str


@dataclass(frozen=True, slots=True)
class SessionCommand:
    """BFF-authenticated opaque session validation request."""

    bff_token: SecretStr
    session_token: SecretStr


@dataclass(frozen=True, slots=True)
class LogoutCommand:
    """Same-origin administrator session revocation request."""

    bff_token: SecretStr
    session_token: SecretStr
    csrf_token: str
    origin: str | None
    referer: str | None


class LoginPayload(BaseModel):
    """Administrator login JSON body sent only by the BFF."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    password: SecretStr
    client_ip: str = Field(min_length=1)


class AdminSessionResponse(BaseModel):
    """Opaque session material consumed only by the BFF server."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    session_token: str | None = None
    expires_at: datetime
    csrf_token: str
    rotated: bool


class AdminAuthHandler(Protocol):
    """Repository-backed administrator auth operations."""

    async def login(self, command: LoginCommand) -> AdminSessionResponse:
        """Create an administrator session after BFF authorization."""
        ...

    async def session(self, command: SessionCommand) -> AdminSessionResponse:
        """Validate and optionally rotate an administrator session."""
        ...

    async def logout(self, command: LogoutCommand) -> None:
        """Revoke an administrator session after CSRF authorization."""
        ...


def _bearer(authorization: str) -> SecretStr:
    if not authorization.startswith("Bearer "):
        raise IdentityError(
            IdentityErrorCode.INVALID_TOKEN, "BFF service token required"
        )
    value = authorization.removeprefix("Bearer ")
    if not value:
        raise IdentityError(
            IdentityErrorCode.INVALID_TOKEN, "BFF service token required"
        )
    return SecretStr(value)


def _private_headers(response: Response, correlation_id: str | None) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Correlation-ID"] = correlation_id_from_header(correlation_id)


def create_auth_router(handler: AdminAuthHandler) -> APIRouter:
    """Create single-admin auth routes around a durable handler."""
    router = APIRouter(prefix="/v1/auth", tags=["auth"])

    @router.post("/login", response_model=AdminSessionResponse)
    async def login(
        payload: LoginPayload,
        response: Response,
        authorization: Annotated[str, Header()],
        correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    ) -> AdminSessionResponse:
        """Verify an administrator password and create an opaque session."""
        result = await handler.login(
            LoginCommand(
                bff_token=_bearer(authorization),
                password=payload.password,
                client_ip=payload.client_ip,
            )
        )
        _private_headers(response, correlation_id)
        return result

    @router.get("/session", response_model=AdminSessionResponse)
    async def session(
        response: Response,
        authorization: Annotated[str, Header()],
        session_token: Annotated[str, Header(alias="X-Admin-Session")],
        correlation_id: Annotated[str | None, Header(alias="X-Correlation-ID")] = None,
    ) -> AdminSessionResponse:
        """Validate and rotate an opaque administrator session when required."""
        result = await handler.session(
            SessionCommand(
                bff_token=_bearer(authorization),
                session_token=SecretStr(session_token),
            )
        )
        _private_headers(response, correlation_id)
        return result

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(
        authorization: Annotated[str, Header()],
        session_token: Annotated[str, Header(alias="X-Admin-Session")],
        csrf_token: Annotated[str, Header(alias="X-CSRF-Token")],
        origin: Annotated[str | None, Header()] = None,
        referer: Annotated[str | None, Header()] = None,
    ) -> Response:
        """Revoke the current session after same-origin CSRF validation."""
        await handler.logout(
            LogoutCommand(
                bff_token=_bearer(authorization),
                session_token=SecretStr(session_token),
                csrf_token=csrf_token,
                origin=origin,
                referer=referer,
            )
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    _ = login, session, logout
    return router
