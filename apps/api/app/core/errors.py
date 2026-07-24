"""Redacted identity errors and HTTP envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique
from typing import TYPE_CHECKING, ClassVar, NewType, assert_never, final, override
from uuid import UUID, uuid4

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

CorrelationId = NewType("CorrelationId", str)


@unique
class IdentityErrorCode(StrEnum):
    """Stable machine-consumed identity failure codes."""

    INVALID_REQUEST = "invalid_request"
    INVALID_TOKEN = "invalid_token"  # noqa: S105 - This is an error code.
    INVALID_CREDENTIAL = "invalid_credential"
    INVALID_CREDENTIAL_VERSION = "invalid_credential_version"
    INVALID_SCOPE = "invalid_scope"
    REPLAYED_NONCE = "replayed_nonce"
    RATE_LIMITED = "rate_limited"
    INVALID_OIDC_CLAIMS = "invalid_oidc_claims"
    STALE_REQUEST = "stale_request"
    SESSION_EXPIRED = "session_expired"
    SESSION_REVOKED = "session_revoked"
    CSRF_REJECTED = "csrf_rejected"
    SERVICE_UNAVAILABLE = "service_unavailable"


@final
@dataclass(frozen=True, slots=True)
class IdentityError(Exception):
    """A redacted identity failure safe to translate at an HTTP boundary."""

    code: IdentityErrorCode
    public_message: str
    retry_after_seconds: int | None = None

    @override
    def __str__(self) -> str:
        """Return the stable redacted error representation."""
        return f"{self.code}: {self.public_message}"


class ErrorBody(BaseModel):
    """Stable API error body."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    code: IdentityErrorCode
    message: str
    correlation_id: str


class ErrorEnvelope(BaseModel):
    """Top-level API error envelope."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    error: ErrorBody


def correlation_id_from_header(value: str | None) -> CorrelationId:
    """Return a valid caller UUID or a newly generated correlation ID."""
    if value is None:
        return CorrelationId(str(uuid4()))
    try:
        parsed = UUID(value)
    except ValueError:
        return CorrelationId(str(uuid4()))
    return CorrelationId(str(parsed))


def status_for_identity_error(code: IdentityErrorCode) -> int:
    """Map identity failures to their stable HTTP status."""
    match code:
        case IdentityErrorCode.INVALID_REQUEST | IdentityErrorCode.INVALID_SCOPE:
            return 422
        case (
            IdentityErrorCode.INVALID_TOKEN
            | IdentityErrorCode.INVALID_CREDENTIAL
            | IdentityErrorCode.INVALID_CREDENTIAL_VERSION
            | IdentityErrorCode.STALE_REQUEST
        ):
            return 401
        case (
            IdentityErrorCode.INVALID_OIDC_CLAIMS
            | IdentityErrorCode.SESSION_EXPIRED
            | IdentityErrorCode.SESSION_REVOKED
            | IdentityErrorCode.CSRF_REJECTED
        ):
            return 403
        case IdentityErrorCode.REPLAYED_NONCE:
            return 409
        case IdentityErrorCode.RATE_LIMITED:
            return 429
        case IdentityErrorCode.SERVICE_UNAVAILABLE:
            return 503
        case _:
            assert_never(code)


def identity_error_response(
    error: IdentityError, correlation_id: CorrelationId
) -> JSONResponse:
    """Serialize a redacted identity error with correlation metadata."""
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=error.code,
            message=error.public_message,
            correlation_id=correlation_id,
        )
    )
    headers = {"X-Correlation-ID": correlation_id, "Cache-Control": "no-store"}
    if error.retry_after_seconds is not None:
        headers["Retry-After"] = str(error.retry_after_seconds)
    return JSONResponse(
        status_code=status_for_identity_error(error.code),
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


async def _identity_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, IdentityError):
        raise error
    return identity_error_response(
        error,
        correlation_id_from_header(request.headers.get("X-Correlation-ID")),
    )


async def _validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    return identity_error_response(
        IdentityError(IdentityErrorCode.INVALID_REQUEST, "request validation failed"),
        correlation_id_from_header(request.headers.get("X-Correlation-ID")),
    )


def install_error_handlers(app: FastAPI) -> None:
    """Install secret-safe handlers for identity and validation failures."""
    app.add_exception_handler(IdentityError, _identity_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)


if TYPE_CHECKING:
    from fastapi import FastAPI, Request
