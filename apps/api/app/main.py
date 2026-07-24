"""FastAPI application boundary for the prediction-market monitor."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ClassVar

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware import Middleware

from app.api.routes.health import HealthResponse
from app.core.errors import (
    IdentityError,
    IdentityErrorCode,
    correlation_id_from_header,
    identity_error_response,
    install_error_handlers,
)
from app.wiring import (
    AppDependencies,
    dependencies_from_environment,
    include_application_routes,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from starlette.middleware.base import RequestResponseEndpoint
    from starlette.types import ASGIApp, Receive, Scope, Send

APP_VERSION = "0.1.0"


class _DenyByDefaultCORS:
    _inner: CORSMiddleware

    def __init__(self, app: ASGIApp) -> None:
        self._inner = CORSMiddleware(
            app,
            allow_origins=[],
            allow_credentials=False,
            allow_methods=[],
            allow_headers=[],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self._inner(scope, receive, send)


def _deny_by_default_cors(app: ASGIApp) -> ASGIApp:
    return _DenyByDefaultCORS(app)


class _GenericErrorBody(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    code: str
    message: str
    correlation_id: str


class _GenericErrorEnvelope(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    error: _GenericErrorBody


def _generic_http_error(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, StarletteHTTPException):
        raise error
    status_codes = {
        400: "invalid_request",
        401: "invalid_token",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        429: "rate_limited",
        500: "service_unavailable",
        503: "service_unavailable",
    }
    messages = {
        400: "request could not be accepted",
        401: "authentication required",
        403: "request is not allowed",
        404: "resource was not found",
        405: "method is not allowed",
        409: "request conflicts with current state",
        429: "request rate limit exceeded",
        500: "service is unavailable",
        503: "service is unavailable",
    }
    status_code = error.status_code
    correlation_id = correlation_id_from_header(request.headers.get("X-Correlation-ID"))
    envelope = _GenericErrorEnvelope(
        error=_GenericErrorBody(
            code=status_codes.get(status_code, "service_unavailable"),
            message=messages.get(status_code, "service is unavailable"),
            correlation_id=correlation_id,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json"),
        headers={"X-Correlation-ID": correlation_id, "Cache-Control": "no-store"},
    )


async def _infrastructure_error(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, (OSError, TimeoutError, SQLAlchemyError)):
        raise error
    return identity_error_response(
        IdentityError(
            IdentityErrorCode.SERVICE_UNAVAILABLE,
            "service dependency is unavailable",
        ),
        correlation_id_from_header(request.headers.get("X-Correlation-ID")),
    )


def create_app(dependencies: AppDependencies | None = None) -> FastAPI:
    """Build the deployed API from production or explicitly injected adapters."""
    resolved = (
        dependencies_from_environment(os.environ)
        if dependencies is None
        else dependencies
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        """Dispose the production database engine when the application stops."""
        try:
            yield
        finally:
            if resolved.sessions is not None:
                await resolved.sessions.close()

    app = FastAPI(
        title="Prediction-market community monitor API",
        version=APP_VERSION,
        middleware=[Middleware(_deny_by_default_cors)],
        lifespan=lifespan,
    )
    app.state.identity_metadata = (
        None if resolved.settings is None else resolved.settings.redacted_metadata()
    )

    @app.middleware("http")
    async def add_correlation_id(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Attach a caller or generated correlation ID to every response."""
        response = await call_next(request)
        if "X-Correlation-ID" not in response.headers:
            response.headers["X-Correlation-ID"] = correlation_id_from_header(
                request.headers.get("X-Correlation-ID")
            )
        return response

    _ = add_correlation_id
    install_error_handlers(app)
    app.add_exception_handler(StarletteHTTPException, _generic_http_error)
    app.add_exception_handler(OSError, _infrastructure_error)
    app.add_exception_handler(TimeoutError, _infrastructure_error)
    app.add_exception_handler(SQLAlchemyError, _infrastructure_error)
    include_application_routes(app, resolved, APP_VERSION)
    return app


app = create_app()

__all__ = ["APP_VERSION", "AppDependencies", "HealthResponse", "app", "create_app"]
