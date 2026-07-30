"""Least-privilege GitHub OIDC claim for durable workflow reservations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Protocol, final

from fastapi import APIRouter, Header, Response
from sqlalchemy import text

from app.core.errors import IdentityError, IdentityErrorCode
from app.core.jwt import TOKEN_SKEW_SECONDS
from app.services.dashboard.security import bearer_token
from app.services.identity.github import (
    GITHUB_OIDC_AUDIENCE,
    GITHUB_OIDC_ISSUER,
    GitHubOIDCClaims,
)
from app.services.release.receipts import canonicalize
from app.services.release.workflow_claim_receipts import build_claim_receipt
from app.services.release.workflow_claims import (
    WorkflowDispatchClaimReceipt,
    WorkflowDispatchClaimRequest,
)

if TYPE_CHECKING:
    from pydantic import SecretStr

    from app.db.session import DatabaseSessions
    from app.services.identity.exchanges import Clock, GitHubOIDCVerifier

CLAIM_RESERVATION_SQL = """
UPDATE release_operation_reservations
SET claimed_run_id = :run_id,
    claimed_run_attempt = :run_attempt,
    claimed_at_db = transaction_timestamp()
WHERE receipt_sha256 = :reservation_sha256
  AND repository = :repository
  AND git_ref = :git_ref
  AND workflow_file = :workflow
  AND event_name = :event
  AND display_title = :display_title
  AND reviewed_sha = :head_sha
  AND head_sha = :head_sha
  AND approved_plan_sha256 = :approved_plan_sha256
  AND activation_nonce = :activation_nonce
  AND dispatch_nonce = :dispatch_nonce
  AND claimed_run_id IS NULL
RETURNING receipt_sha256, reviewed_sha, approved_plan_sha256,
          approval_round_id, approval_launch_sha256s, activation_nonce,
          dispatch_nonce, attempt, reserved_at_db, selection_floor_at,
          claimed_at_db
"""

INSERT_CLAIM_RECEIPT_SQL = """
INSERT INTO release_receipt_chain (
    receipt_sha256, canonical_receipt, command, reviewed_sha,
    approved_plan_sha256, approval_round_id, approval_launch_sha256s,
    activation_nonce, dispatch_nonce, attempt, accepted,
    terminal_for_attempt, retry_permitted, predecessor_receipt_sha256,
    created_at_db
) VALUES (
    :receipt_sha256, :canonical_receipt, 'workflow-dispatch-claim',
    :reviewed_sha, :approved_plan_sha256, :approval_round_id,
    CAST(:approval_launch_sha256s AS jsonb), :activation_nonce,
    :dispatch_nonce, :attempt, true, false, false,
    :predecessor_receipt_sha256, :created_at_db
)
"""


class WorkflowDispatchClaimer(Protocol):
    """Port used by the HTTP route without exposing a database credential."""

    async def claim(
        self,
        token: SecretStr,
        payload: WorkflowDispatchClaimRequest,
    ) -> WorkflowDispatchClaimReceipt:
        """Claim one reservation or fail without mutating another run."""
        ...


@final
class WorkflowDispatchClaimOidcAuthorizer:
    """Bind the raw OIDC token to the exact request workflow and run."""

    def __init__(
        self,
        *,
        verifier: GitHubOIDCVerifier,
        clock: Clock,
        repository: str,
    ) -> None:
        """Bind cryptographic verification to one repository."""
        self._verifier = verifier
        self._clock = clock
        self._repository = repository

    async def authorize(
        self,
        token: SecretStr,
        payload: WorkflowDispatchClaimRequest,
    ) -> GitHubOIDCClaims:
        """Verify and bind every authenticated GitHub run identity claim."""
        now = self._clock.now()
        claims = await self._verifier.verify(token, now)
        now_seconds = int(now.timestamp())
        subject_suffix = (
            f"environment:{payload.environment}"
            if payload.environment is not None
            else f"ref:{payload.ref}"
        )
        matches = (
            claims.issuer == GITHUB_OIDC_ISSUER
            and claims.audience == GITHUB_OIDC_AUDIENCE
            and claims.subject == f"repo:{payload.repository}:{subject_suffix}"
            and claims.repository == self._repository == payload.repository
            and claims.job_workflow_ref
            == (
                f"{payload.repository}/.github/workflows/"
                f"{payload.workflow}@{payload.ref}"
            )
            and claims.git_ref == payload.ref
            and claims.head_sha == payload.head_sha
            and claims.environment == payload.environment
            and claims.run_id == str(payload.run_id)
            and claims.run_attempt == str(payload.run_attempt)
            and claims.issued_at <= now_seconds + TOKEN_SKEW_SECONDS
            and claims.not_before <= now_seconds + TOKEN_SKEW_SECONDS
            and claims.expires_at >= now_seconds - TOKEN_SKEW_SECONDS
            and claims.expires_at > claims.not_before
        )
        if not matches:
            raise IdentityError(
                IdentityErrorCode.INVALID_OIDC_CLAIMS,
                "workflow dispatch GitHub identity rejected",
            )
        return claims


@final
class SqlWorkflowDispatchClaimer:
    """Atomically claim and append a canonical public receipt."""

    def __init__(
        self,
        sessions: DatabaseSessions,
        oidc: WorkflowDispatchClaimOidcAuthorizer,
    ) -> None:
        """Bind database session ownership and exact OIDC policy."""
        self._sessions = sessions
        self._oidc = oidc

    async def claim(
        self,
        token: SecretStr,
        payload: WorkflowDispatchClaimRequest,
    ) -> WorkflowDispatchClaimReceipt:
        """Claim one exact reservation and append its public receipt."""
        _ = await self._oidc.authorize(token, payload)
        parameters = {
            **payload.model_dump(mode="python"),
            "git_ref": payload.ref,
        }
        async with self._sessions.open() as session, session.begin():
            result = await session.execute(text(CLAIM_RESERVATION_SQL), parameters)
            row = result.mappings().one_or_none()
            if row is None:
                raise IdentityError(
                    IdentityErrorCode.INVALID_CREDENTIAL,
                    "workflow dispatch reservation rejected",
                )
            receipt = build_claim_receipt(payload, dict(row))
            _ = await session.execute(
                text(INSERT_CLAIM_RECEIPT_SQL),
                {
                    **receipt.model_dump(mode="python"),
                    "canonical_receipt": canonicalize(
                        receipt.model_dump(mode="json", by_alias=True)
                    ),
                    "approval_launch_sha256s": canonicalize(
                        list(receipt.approval_launch_sha256s)
                    ).decode(),
                    "predecessor_receipt_sha256": receipt.reservation_sha256,
                    "created_at_db": receipt.database_timestamps.claimed_at_db,
                },
            )
        return receipt


@final
class UnavailableWorkflowDispatchClaimer:
    """Fail closed when the deployed claim adapter is unavailable."""

    async def claim(
        self,
        token: SecretStr,
        payload: WorkflowDispatchClaimRequest,
    ) -> WorkflowDispatchClaimReceipt:
        """Reject without inspecting or retaining the raw token."""
        del token, payload
        raise IdentityError(
            IdentityErrorCode.SERVICE_UNAVAILABLE,
            "workflow dispatch claimer is unavailable",
        )


def create_workflow_dispatch_claim_router(
    claimer: WorkflowDispatchClaimer,
) -> APIRouter:
    """Create the least-privilege workflow dispatch claim route."""
    router = APIRouter(prefix="/internal/release", tags=["release"])

    @router.post(
        "/workflow-dispatch-claim",
        response_model=WorkflowDispatchClaimReceipt,
    )
    async def claim(
        payload: WorkflowDispatchClaimRequest,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> WorkflowDispatchClaimReceipt:
        receipt = await claimer.claim(bearer_token(authorization), payload)
        response.headers["Cache-Control"] = "no-store"
        return receipt

    _ = claim
    return router


__all__ = (
    "CLAIM_RESERVATION_SQL",
    "SqlWorkflowDispatchClaimer",
    "UnavailableWorkflowDispatchClaimer",
    "WorkflowDispatchClaimOidcAuthorizer",
    "WorkflowDispatchClaimer",
    "create_workflow_dispatch_claim_router",
)
