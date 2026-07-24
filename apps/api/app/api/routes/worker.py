"""Version-bound Windows analysis worker routes."""

from __future__ import annotations

import json
from datetime import datetime  # noqa: TC003 - Pydantic resolves this at runtime.
from enum import StrEnum, unique
from typing import (
    TYPE_CHECKING,
    Annotated,
    ClassVar,
    Literal,
    Protocol,
    Self,
    assert_never,
)
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from fastapi import APIRouter, Header, Response, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.analysis.output import AnalysisOutput
from app.core.principals import Scope
from app.services.dashboard.security import require_scope

if TYPE_CHECKING:
    from app.domain.types import JsonValue
    from app.services.dashboard.models import AuthorizedService
    from app.services.dashboard.ports import ScopeAuthorizer

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
LeaseToken = Annotated[
    str,
    StringConstraints(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]


class _WorkerModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class WorkerLeaseRequest(_WorkerModel):
    """Current capability proof requested by the authenticated worker."""

    capability_proof_id: str = Field(min_length=1, max_length=200)


class AnalysisLeaseProof(_WorkerModel):
    """Exact queue, content, model, schema, and lease CAS tuple."""

    item_id: UUID
    post_version_id: UUID
    content_hash: Sha256
    prompt_version: str = Field(min_length=1, max_length=100)
    model_version: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(min_length=1, max_length=100)
    lease_token: LeaseToken


class WorkerLeaseItem(AnalysisLeaseProof):
    """Author-free analysis input and one response-only raw lease token."""

    title: str
    body: str
    language: Literal["ko", "en"]
    lease_expires_at: datetime


class WorkerLeaseGranted(_WorkerModel):
    """One version-bound work item leased to the worker."""

    outcome: Literal["leased"] = "leased"
    item: WorkerLeaseItem


class WorkerLeaseBlocked(_WorkerModel):
    """Capability-blocked response that cannot contain work or a lease token."""

    outcome: Literal["blocked_capability"] = "blocked_capability"
    reason_codes: tuple[str, ...] = Field(min_length=1)


class WorkerLeaseEmpty(_WorkerModel):
    """Honest empty queue response without synthetic analysis work."""

    outcome: Literal["empty"] = "empty"
    retry_after_seconds: int = Field(ge=1, le=600)


WorkerLeaseResponse = Annotated[
    WorkerLeaseGranted | WorkerLeaseBlocked | WorkerLeaseEmpty,
    Field(discriminator="outcome"),
]


class WorkerHeartbeatPayload(AnalysisLeaseProof):
    """CAS tuple required to extend an active analysis lease."""


class WorkerHeartbeatResult(_WorkerModel):
    """Server-owned expiry returned after a successful heartbeat."""

    lease_expires_at: datetime


@unique
class AckKind(StrEnum):
    """Worker acknowledgment outcomes accepted by the queue boundary."""

    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"


class WorkerAckPayload(AnalysisLeaseProof):
    """Success output or retryable failure bound to the original lease tuple."""

    kind: AckKind
    output: AnalysisOutput | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("output", mode="before")
    @classmethod
    def parse_strict_output(
        cls, value: AnalysisOutput | JsonValue | float
    ) -> AnalysisOutput | None:
        """Parse nested HTTP JSON through the strict domain JSON boundary."""
        if isinstance(value, list):
            error_code = "invalid_analysis_output"
            raise PydanticCustomError(error_code, "analysis output is invalid")
        match value:  # noqa: RUF100  # noqa: MATCH_OK
            case None | AnalysisOutput():
                return value
            case dict():
                return AnalysisOutput.model_validate_json(json.dumps(value))
            case str() | int() | float():
                error_code = "invalid_analysis_output"
                raise PydanticCustomError(error_code, "analysis output is invalid")
        assert_never(value)

    @model_validator(mode="after")
    def require_ack_variant(self) -> Self:
        """Reject ambiguous success and failure acknowledgments."""
        kind = self.kind
        match kind:  # noqa: RUF100  # noqa: MATCH_OK
            case AckKind.SUCCESS:
                if self.output is None or self.error_code is not None:
                    error_code = "invalid_success_ack"
                    raise PydanticCustomError(
                        error_code, "success ack requires only output"
                    )
                return self
            case AckKind.RETRYABLE_FAILURE:
                if self.output is not None or self.error_code is None:
                    error_code = "invalid_failure_ack"
                    raise PydanticCustomError(
                        error_code, "failure ack requires only error code"
                    )
                return self
        assert_never(kind)


class WorkerHandler(Protocol):
    """Atomically lease, heartbeat, and acknowledge version-bound queue work."""

    async def lease(
        self, principal: AuthorizedService, request: WorkerLeaseRequest
    ) -> WorkerLeaseResponse:
        """Lease one item or return an explicit blocked or empty outcome."""
        ...

    async def heartbeat(
        self, principal: AuthorizedService, payload: WorkerHeartbeatPayload
    ) -> WorkerHeartbeatResult:
        """Extend only the caller's active exact-tuple lease."""
        ...

    async def ack(
        self, principal: AuthorizedService, payload: WorkerAckPayload
    ) -> None:
        """Persist one terminal or retryable queue acknowledgment."""
        ...


def create_worker_router(
    authorizer: ScopeAuthorizer, handler: WorkerHandler
) -> APIRouter:
    """Create worker routes with distinct least-privilege scopes."""
    router = APIRouter(prefix="/v1/worker", tags=["worker"])

    @router.post("/lease", response_model=WorkerLeaseResponse)
    async def lease(
        payload: WorkerLeaseRequest,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> WorkerLeaseResponse:
        """Lease work only after worker:lease authorization."""
        principal = await require_scope(authorizer, authorization, Scope.WORKER_LEASE)
        result = await handler.lease(principal, payload)
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.post("/heartbeat", response_model=WorkerHeartbeatResult)
    async def heartbeat(
        payload: WorkerHeartbeatPayload,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> WorkerHeartbeatResult:
        """Extend an exact active lease after worker:heartbeat authorization."""
        principal = await require_scope(
            authorizer, authorization, Scope.WORKER_HEARTBEAT
        )
        result = await handler.heartbeat(principal, payload)
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.post("/ack", status_code=status.HTTP_204_NO_CONTENT)
    async def ack(
        payload: WorkerAckPayload,
        authorization: Annotated[str | None, Header()] = None,
    ) -> Response:
        """Acknowledge an exact active lease after worker:ack authorization."""
        principal = await require_scope(authorizer, authorization, Scope.WORKER_ACK)
        await handler.ack(principal, payload)
        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "no-store"},
        )

    _ = lease, heartbeat, ack
    return router
