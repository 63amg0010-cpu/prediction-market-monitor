"""Scoped, read-only activation-evidence verification endpoint."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated, ClassVar, Protocol, final
from uuid import UUID  # noqa: TC003 - Pydantic resolves runtime row fields.

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, ConfigDict
from scripts.activation_evidence_models import (
    ActivationEvidenceReceipt,
    ActivationEvidenceVerifyRequest,
    canonical_attestation_bytes,
)
from sqlalchemy import text

from app.core.errors import IdentityError, IdentityErrorCode
from app.core.jwt import TOKEN_SKEW_SECONDS
from app.services.dashboard.security import bearer_token
from app.services.identity.github import (
    GITHUB_OIDC_AUDIENCE,
    GITHUB_OIDC_ISSUER,
    GitHubOIDCClaims,
)

if TYPE_CHECKING:
    from pydantic import SecretStr

    from app.db.session import DatabaseSessions
    from app.services.identity.exchanges import Clock, GitHubOIDCVerifier

_MAX_EVIDENCE_AGE = timedelta(hours=2)
RESERVATION_READ_SQL = """
SELECT
    transaction_timestamp() AS database_time,
    reservation.receipt_sha256,
    reservation.activation_nonce,
    reservation.dispatch_nonce,
    reservation.reviewed_sha,
    reservation.attempt,
    reservation.claimed_run_id
FROM release_operation_reservations AS reservation
WHERE reservation.receipt_sha256 = :reservation_receipt_sha256
  AND reservation.revision = '20260727_0011'
"""


class _ActivationReservationRow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    database_time: datetime
    receipt_sha256: str
    activation_nonce: UUID
    dispatch_nonce: UUID
    reviewed_sha: str
    attempt: int
    claimed_run_id: int | None


class ActivationEvidenceVerifier(Protocol):
    """Read-only verifier for one exact workflow reservation and attestation."""

    async def verify(
        self,
        token: SecretStr,
        payload: ActivationEvidenceVerifyRequest,
    ) -> ActivationEvidenceReceipt:
        """Return a public receipt or reject without exposing credentials."""
        ...


@final
class ActivationEvidenceOidcAuthorizer:
    """Authorize only the protected-main activation-evidence GitHub workflow."""

    def __init__(
        self,
        *,
        verifier: GitHubOIDCVerifier,
        clock: Clock,
        repository: str,
    ) -> None:
        """Bind cryptographic verification to one repository workflow policy."""
        self._verifier = verifier
        self._clock = clock
        self._repository = repository

    async def authorize(
        self,
        token: SecretStr,
        payload: ActivationEvidenceVerifyRequest,
    ) -> GitHubOIDCClaims:
        """Verify the raw OIDC JWT and bind every workflow-run identity field."""
        now = self._clock.now()
        claims = await self._verifier.verify(token, now)
        now_seconds = int(now.timestamp())
        expected_subject = (
            f"repo:{self._repository}:environment:production-collector"
        )
        expected_workflow = (
            f"{self._repository}/.github/workflows/"
            "activation-evidence.yml@refs/heads/main"
        )
        time_valid = (
            claims.issued_at <= now_seconds + TOKEN_SKEW_SECONDS
            and claims.not_before <= now_seconds + TOKEN_SKEW_SECONDS
            and claims.expires_at >= now_seconds - TOKEN_SKEW_SECONDS
            and claims.expires_at > claims.not_before
        )
        matches = (
            claims.issuer == GITHUB_OIDC_ISSUER
            and claims.audience == GITHUB_OIDC_AUDIENCE
            and claims.subject == expected_subject
            and claims.repository == self._repository
            and claims.job_workflow_ref == expected_workflow
            and claims.git_ref == "refs/heads/main"
            and claims.environment == "production-collector"
            and claims.run_id == str(payload.run_id)
            and claims.run_attempt == str(payload.run_attempt)
            and claims.head_sha == payload.head_sha
            and time_valid
        )
        if not matches:
            raise IdentityError(
                IdentityErrorCode.INVALID_OIDC_CLAIMS,
                "activation evidence GitHub identity rejected",
            )
        return claims


@final
class SqlActivationEvidenceVerifier:
    """Verify activation evidence inside a read-only repeatable-read transaction."""

    def __init__(
        self,
        sessions: DatabaseSessions,
        oidc: ActivationEvidenceOidcAuthorizer,
    ) -> None:
        """Bind the database session owner and endpoint-specific OIDC policy."""
        self._sessions = sessions
        self._oidc = oidc

    async def verify(
        self,
        token: SecretStr,
        payload: ActivationEvidenceVerifyRequest,
    ) -> ActivationEvidenceReceipt:
        """Authorize, bind canonical bytes, and read the reserved run."""
        _ = await self._oidc.authorize(token, payload)
        canonical = canonical_attestation_bytes(payload.attestation)
        if hashlib.sha256(canonical).hexdigest() != payload.attestation_sha256:
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "activation evidence binding rejected",
            )
        async with self._sessions.open() as session:
            _ = await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            result = await session.execute(
                text(RESERVATION_READ_SQL),
                {"reservation_receipt_sha256": payload.reservation_receipt_sha256},
            )
            raw_row = result.mappings().one_or_none()
        if raw_row is None:
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "activation evidence reservation rejected",
            )
        row = _ActivationReservationRow.model_validate(raw_row)
        attestation = payload.attestation
        database_time = row.database_time
        matches = (
            row.activation_nonce == attestation.activation_nonce
            and row.dispatch_nonce == payload.dispatch_nonce
            and row.reviewed_sha == attestation.reviewed_sha == payload.head_sha
            and row.attempt == payload.attempt
            and row.claimed_run_id == payload.run_id
            and timedelta(0)
            <= database_time - attestation.captured_at
            < _MAX_EVIDENCE_AGE
            and timedelta(0)
            <= database_time - attestation.evidence_database_time
            < _MAX_EVIDENCE_AGE
        )
        if not matches:
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "activation evidence identity rejected",
            )
        return ActivationEvidenceReceipt(
            activation_nonce=attestation.activation_nonce,
            attestation_generation=attestation.attestation_generation,
            attestation_sha256=payload.attestation_sha256,
            reservation_receipt_sha256=payload.reservation_receipt_sha256,
            dispatch_nonce=payload.dispatch_nonce,
            attempt=payload.attempt,
            run_id=payload.run_id,
            run_attempt=payload.run_attempt,
            head_sha=payload.head_sha,
            database_time=database_time,
        )


@final
class UnavailableActivationEvidenceVerifier:
    """Fail closed when the scoped read-only adapter is unavailable."""

    async def verify(
        self,
        token: SecretStr,
        payload: ActivationEvidenceVerifyRequest,
    ) -> ActivationEvidenceReceipt:
        """Reject without inspecting or retaining the supplied secret token."""
        del token, payload
        raise IdentityError(
            IdentityErrorCode.SERVICE_UNAVAILABLE,
            "activation evidence verifier is unavailable",
        )


def create_activation_evidence_router(
    verifier: ActivationEvidenceVerifier,
) -> APIRouter:
    """Create the sole activation-evidence verification HTTP surface."""
    router = APIRouter(prefix="/internal/release", tags=["release"])

    @router.post(
        "/activation-evidence-verify",
        response_model=ActivationEvidenceReceipt,
    )
    async def verify(
        payload: ActivationEvidenceVerifyRequest,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ActivationEvidenceReceipt:
        receipt = await verifier.verify(bearer_token(authorization), payload)
        response.headers["Cache-Control"] = "no-store"
        return receipt

    _ = verify
    return router


__all__ = (
    "RESERVATION_READ_SQL",
    "ActivationEvidenceOidcAuthorizer",
    "ActivationEvidenceVerifier",
    "SqlActivationEvidenceVerifier",
    "UnavailableActivationEvidenceVerifier",
    "create_activation_evidence_router",
)
