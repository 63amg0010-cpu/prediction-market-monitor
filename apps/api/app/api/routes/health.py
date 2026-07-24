"""Redacted application and database liveness route."""

from __future__ import annotations

from typing import ClassVar, Literal, Protocol, assert_never

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from app.services.dashboard.models import DatabaseStatus


class HealthResponse(BaseModel):
    """Secret-free liveness and dependency availability response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    status: Literal["ok", "degraded"]
    version: str
    db: DatabaseStatus


class DatabaseHealthProbe(Protocol):
    """Probe database availability without exposing the failure cause."""

    async def database_status(self) -> DatabaseStatus:
        """Return only an allowlisted redacted status."""
        ...


def create_health_router(probe: DatabaseHealthProbe, *, version: str) -> APIRouter:
    """Create a public health route backed by an injected real probe."""
    router = APIRouter(tags=["health"])

    @router.get("/v1/health", response_model=HealthResponse)
    async def health(response: Response) -> HealthResponse:
        """Report dependency availability without error or configuration details."""
        db_status = await probe.database_status()
        response.headers["Cache-Control"] = "no-store"
        match db_status:  # noqa: RUF100  # noqa: MATCH_OK
            case DatabaseStatus.OK:
                return HealthResponse(status="ok", version=version, db=db_status)
            case DatabaseStatus.UNAVAILABLE:
                return HealthResponse(status="degraded", version=version, db=db_status)
        assert_never(db_status)

    _ = health
    return router
