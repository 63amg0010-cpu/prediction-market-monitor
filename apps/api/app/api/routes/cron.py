"""Static-secret Vercel daily cron route."""

from __future__ import annotations

from datetime import date, datetime  # noqa: TC003 - Pydantic resolves at runtime.
from enum import StrEnum, unique
from typing import TYPE_CHECKING, Annotated, ClassVar, Protocol, Self

from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

if TYPE_CHECKING:
    from app.services.identity.cron import CronCredentialVerifier


@unique
class DailyOutcomeStatus(StrEnum):
    """Independent bounded report and retention job outcomes."""

    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


class DailyOutcome(BaseModel):
    """Redacted report and retention outcomes for one Seoul date."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    target_date_seoul: date
    report: DailyOutcomeStatus
    retention: DailyOutcomeStatus
    error_codes: tuple[str, ...] = Field(max_length=10)


class DailyCronResponse(BaseModel):
    """At most seven ascending catch-up dates processed by one invocation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    started_at: datetime
    finished_at: datetime
    outcomes: tuple[DailyOutcome, ...] = Field(max_length=7)

    @model_validator(mode="after")
    def require_bounded_order(self) -> Self:
        """Reject reversed time anchors or non-ascending catch-up dates."""
        dates = tuple(outcome.target_date_seoul for outcome in self.outcomes)
        if self.finished_at < self.started_at or dates != tuple(sorted(set(dates))):
            error_code = "invalid_daily_bounds"
            raise PydanticCustomError(
                error_code, "daily outcomes must be unique and ascending"
            )
        return self


class DailyCronHandler(Protocol):
    """Run bounded report and retention reconciliation using durable jobs."""

    async def run_daily(self) -> DailyCronResponse:
        """Return redacted outcomes for no more than seven dates."""
        ...


def create_cron_router(
    verifier: CronCredentialVerifier, handler: DailyCronHandler
) -> APIRouter:
    """Create the body-free Vercel GET cron route."""
    router = APIRouter(tags=["cron"])

    @router.get("/api/cron/daily", response_model=DailyCronResponse)
    async def daily(
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> DailyCronResponse:
        """Validate the static secret before invoking bounded reconciliation."""
        verifier.verify(authorization)
        result = await handler.run_daily()
        response.headers["Cache-Control"] = "no-store"
        return result

    _ = daily
    return router
