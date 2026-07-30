"""OIDC-authenticated durable cadence attempt recording endpoint."""

from __future__ import annotations

from typing import (
    TYPE_CHECKING,
    Annotated,
    cast,
    final,
)

from fastapi import APIRouter, Header, Response

from app.api.routes.workflow_dispatch_claim import (
    WorkflowDispatchClaimOidcAuthorizer,
)
from app.core.errors import IdentityError, IdentityErrorCode
from app.services.dashboard.security import bearer_token
from app.services.release.cadence_workflow_models import (
    CadenceWorkflowAttemptReceipt,
    CadenceWorkflowAttemptRequest,
    CadenceWorkflowRecorder,
    SourceResult,
)
from app.services.release.cadence_workflow_recorder import (
    SqlCadenceWorkflowRecorder,
)

if TYPE_CHECKING:
    from pydantic import SecretStr

    from app.services.identity.exchanges import Clock, GitHubOIDCVerifier
    from app.services.identity.github import GitHubOIDCClaims
    from app.services.release.workflow_claims import WorkflowDispatchClaimRequest


@final
class CadenceWorkflowOidcAuthorizer:
    """Reuse the reviewed exact GitHub identity checks for cadence payloads."""

    def __init__(
        self,
        *,
        verifier: GitHubOIDCVerifier,
        clock: Clock,
        repository: str,
    ) -> None:
        """Compose the existing audited verifier without widening its API."""
        self._inner = WorkflowDispatchClaimOidcAuthorizer(
            verifier=verifier,
            clock=clock,
            repository=repository,
        )

    async def authorize(
        self,
        token: SecretStr,
        payload: CadenceWorkflowAttemptRequest,
    ) -> GitHubOIDCClaims:
        """Adapt only the structurally shared identity fields."""
        shared = cast(
            "WorkflowDispatchClaimRequest",
            cast("object", payload),
        )
        return await self._inner.authorize(token, shared)


class UnavailableCadenceWorkflowRecorder:
    """Fail closed when Production dependencies are incomplete."""

    async def record(
        self, token: SecretStr, payload: CadenceWorkflowAttemptRequest
    ) -> CadenceWorkflowAttemptReceipt:
        """Reject without retaining token or payload."""
        del token, payload
        raise IdentityError(
            IdentityErrorCode.INVALID_CREDENTIAL,
            "cadence_workflow_recorder_unavailable",
        )


def create_cadence_workflow_router(recorder: CadenceWorkflowRecorder) -> APIRouter:
    """Expose the single no-store internal recording endpoint."""
    router = APIRouter(prefix="/internal/release", tags=["release"])

    @router.post(
        "/cadence-workflow-attempt",
        response_model=CadenceWorkflowAttemptReceipt,
    )
    async def record(
        payload: CadenceWorkflowAttemptRequest,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> CadenceWorkflowAttemptReceipt:
        result = await recorder.record(bearer_token(authorization), payload)
        response.headers["Cache-Control"] = "no-store"
        return result

    _ = record
    return router


__all__ = (
    "CadenceWorkflowAttemptReceipt",
    "CadenceWorkflowAttemptRequest",
    "CadenceWorkflowOidcAuthorizer",
    "SourceResult",
    "SqlCadenceWorkflowRecorder",
    "UnavailableCadenceWorkflowRecorder",
    "create_cadence_workflow_router",
)
