"""Independent freshness verifier snapshot and observation routes."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this at runtime.
from typing import (
    TYPE_CHECKING,
    Annotated,
    ClassVar,
    Final,
    Protocol,
    Self,
    assert_never,
)
from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from fastapi import APIRouter, Header, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from pydantic_core import PydanticCustomError

from app.core.principals import Scope
from app.domain.enums import Country, VerificationStatus
from app.services.dashboard.models import (  # noqa: TC001 - Pydantic runtime field.
    OutcomeStatus,
)
from app.services.dashboard.security import require_scope

if TYPE_CHECKING:
    from app.services.dashboard.ports import ScopeAuthorizer

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
MAX_VERIFICATION_LATENCY_SECONDS: Final = 3 * 60 * 60


class _VerificationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class VerificationSourceSnapshot(_VerificationModel):
    """One source's collection and visible publication state."""

    source_id: UUID
    country: Country
    enabled: bool
    status: OutcomeStatus
    latest_successful_run_id: UUID | None
    latest_successful_run_finished_at: datetime | None
    collection_recency_seconds: int | None = Field(ge=0)
    visible_publication_manifest_id: UUID | None
    visible_publication_sequence: int | None = Field(ge=1)
    publication_first_visible_at: datetime | None


class VerificationSnapshot(_VerificationModel):
    """Consistent no-store snapshot used by the independent verifier."""

    snapshot_id: UUID
    scope_version: str = Field(min_length=1, max_length=80)
    published_at: datetime
    checksum: Sha256
    sources: tuple[VerificationSourceSnapshot, ...]


class VerificationSourceResult(_VerificationModel):
    """S/C/P measurements for one source in one expected verifier slot."""

    source_id: UUID
    scheduler_latency_seconds: int = Field(ge=0)
    collection_recency_seconds: int | None = Field(ge=0)
    publication_latency_seconds: int | None = Field(ge=0)
    status: VerificationStatus
    failure_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def require_status_evidence(self) -> Self:
        """Require complete bounded clocks only for a passing observation."""
        match self.status:
            case VerificationStatus.PASSED:
                if (
                    self.collection_recency_seconds is None
                    or self.publication_latency_seconds is None
                    or self.collection_recency_seconds
                    > MAX_VERIFICATION_LATENCY_SECONDS
                    or self.publication_latency_seconds
                    > MAX_VERIFICATION_LATENCY_SECONDS
                    or self.failure_code is not None
                ):
                    error_code = "invalid_pass_evidence"
                    raise PydanticCustomError(
                        error_code,
                        "passed verification requires bounded S/C/P evidence",
                    )
            case VerificationStatus.FAILED | VerificationStatus.MISSING:
                if self.failure_code is None:
                    error_code = "failure_code_required"
                    raise PydanticCustomError(
                        error_code,
                        "failed verification requires a failure code",
                    )
            case _:
                assert_never(self.status)
        return self


class VerificationObservationPayload(_VerificationModel):
    """One expected slot bound to one immutable snapshot checksum."""

    scope_version: str = Field(min_length=1, max_length=80)
    expected_slot_utc: datetime
    action_started_at: datetime
    snapshot_id: UUID
    snapshot_checksum: Sha256
    source_results: tuple[VerificationSourceResult, ...] = Field(
        min_length=1, max_length=20
    )

    @model_validator(mode="after")
    def require_unique_sources(self) -> Self:
        """Reject duplicate sources before the atomic persistence boundary."""
        source_ids = tuple(result.source_id for result in self.source_results)
        if len(source_ids) != len(set(source_ids)):
            error_code = "duplicate_source_result"
            raise PydanticCustomError(error_code, "source results must be unique")
        return self


class ObservationAccepted(_VerificationModel):
    """Bounded receipt for one accepted expected-slot observation."""

    expected_slot_utc: datetime
    accepted_source_count: int = Field(ge=1, le=20)


class VerificationHandler(Protocol):
    """Read a consistent snapshot and atomically persist verifier evidence."""

    async def snapshot(self) -> VerificationSnapshot:
        """Build one database-consistent verifier snapshot."""
        ...

    async def record(
        self, payload: VerificationObservationPayload
    ) -> ObservationAccepted:
        """Persist one slot or raise a typed conflict."""
        ...


def create_verification_router(
    authorizer: ScopeAuthorizer, handler: VerificationHandler
) -> APIRouter:
    """Create verifier read and write routes with exact scopes."""
    router = APIRouter(prefix="/v1/verification", tags=["verification"])

    @router.get("/snapshot", response_model=VerificationSnapshot)
    async def snapshot(
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> VerificationSnapshot:
        """Return a no-store snapshot after verify:read authorization."""
        _ = await require_scope(authorizer, authorization, Scope.VERIFY_READ)
        result = await handler.snapshot()
        response.headers["Cache-Control"] = "no-store"
        return result

    @router.post(
        "/observations",
        response_model=ObservationAccepted,
        status_code=status.HTTP_201_CREATED,
    )
    async def observations(
        payload: VerificationObservationPayload,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ObservationAccepted:
        """Persist one expected-slot observation after verify:write authorization."""
        _ = await require_scope(authorizer, authorization, Scope.VERIFY_WRITE)
        result = await handler.record(payload)
        response.headers["Cache-Control"] = "no-store"
        return result

    _ = snapshot, observations
    return router
