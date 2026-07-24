from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import httpx2
import pytest
from app.analysis.output import AnalysisOutput
from app.api.routes import worker as worker_routes
from app.api.routes.cron import (
    DailyCronResponse,
    DailyOutcome,
    DailyOutcomeStatus,
    create_cron_router,
)
from app.api.routes.health import (
    HealthResponse,
    create_health_router,
)
from app.core.errors import install_error_handlers
from app.core.principals import PrincipalId, Scope
from app.db.session import DatabaseConfigurationError, DatabaseSessions
from app.services.dashboard.models import AuthorizedService, DatabaseStatus
from app.services.dashboard.sql_health import SqlAlchemyHealthProbe
from app.services.identity.cron import CronCredentialVerifier
from fastapi import FastAPI
from pydantic import SecretStr

NOW = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)
ITEM_ID = UUID("b13178b9-5925-4f4c-821e-84e960e3aa11")


class _CronHandler:
    async def run_daily(self) -> DailyCronResponse:
        return DailyCronResponse(
            started_at=NOW,
            finished_at=NOW,
            outcomes=tuple(
                DailyOutcome(
                    target_date_seoul=date(2026, 7, day),
                    report=DailyOutcomeStatus.SUCCEEDED,
                    retention=DailyOutcomeStatus.SUCCEEDED,
                    error_codes=(),
                )
                for day in range(15, 22)
            ),
        )


class _WorkerScopeAuthorizer:
    calls: list[tuple[str, Scope]]

    def __init__(self) -> None:
        self.calls = []

    async def authorize(
        self, token: SecretStr, required_scope: Scope
    ) -> AuthorizedService:
        self.calls.append((token.get_secret_value(), required_scope))
        return AuthorizedService(
            principal_id=PrincipalId("service:worker"),
            scopes=frozenset({required_scope}),
        )


class _WorkerHandler:
    ack_payload: worker_routes.WorkerAckPayload | None = None

    async def lease(
        self,
        principal: AuthorizedService,
        request: worker_routes.WorkerLeaseRequest,
    ) -> worker_routes.WorkerLeaseBlocked:
        del principal, request
        return worker_routes.WorkerLeaseBlocked(reason_codes=("zero_tools_unproven",))

    async def heartbeat(
        self,
        principal: AuthorizedService,
        payload: worker_routes.WorkerHeartbeatPayload,
    ) -> worker_routes.WorkerHeartbeatResult:
        del principal, payload
        return worker_routes.WorkerHeartbeatResult(
            lease_expires_at=NOW + timedelta(minutes=10)
        )

    async def ack(
        self,
        principal: AuthorizedService,
        payload: worker_routes.WorkerAckPayload,
    ) -> None:
        del principal
        self.ack_payload = payload


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(
        create_cron_router(CronCredentialVerifier(SecretStr("c" * 32)), _CronHandler())
    )
    app.include_router(
        create_health_router(SqlAlchemyHealthProbe(None), version="0.1.0")
    )
    return app


def _worker_app(authorizer: _WorkerScopeAuthorizer, handler: _WorkerHandler) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(worker_routes.create_worker_router(authorizer, handler))
    return app


@pytest.mark.asyncio
async def test_daily_cron_requires_exact_static_bearer_and_is_bounded() -> None:
    # Given
    app = _app()

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        rejected = await client.get(
            "/api/cron/daily", headers={"Authorization": f"Bearer {'x' * 32}"}
        )
        accepted = await client.get(
            "/api/cron/daily", headers={"Authorization": f"Bearer {'c' * 32}"}
        )

    # Then
    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted.headers["cache-control"] == "no-store"
    accepted_body = DailyCronResponse.model_validate_json(accepted.content)
    assert len(accepted_body.outcomes) == 7


@pytest.mark.asyncio
async def test_health_reports_unavailable_database_without_details() -> None:
    # Given
    app = _app()

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        response = await client.get("/v1/health")

    # Then
    assert response.status_code == 200
    assert response.json() == HealthResponse(
        status="degraded", version="0.1.0", db=DatabaseStatus.UNAVAILABLE
    ).model_dump(mode="json")
    assert "url" not in response.text.lower()
    assert "secret" not in response.text.lower()


def test_database_sessions_fail_closed_without_explicit_url() -> None:
    # Given / When
    with pytest.raises(DatabaseConfigurationError) as captured:
        _ = DatabaseSessions.from_environment({})

    # Then
    assert captured.value.code == "database_url_missing"


@pytest.mark.asyncio
async def test_worker_blocked_lease_has_no_work_or_token_and_ack_is_204() -> None:
    # Given
    scopes = _WorkerScopeAuthorizer()
    worker = _WorkerHandler()
    app = _worker_app(scopes, worker)

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        lease = await client.post(
            "/v1/worker/lease",
            headers={"Authorization": "Bearer worker-token"},
            json={"capability_proof_id": "proof-v1"},
        )
        ack = await client.post(
            "/v1/worker/ack",
            headers={"Authorization": "Bearer worker-token"},
            json={
                "kind": "success",
                "item_id": str(ITEM_ID),
                "post_version_id": "848cc2f3-2108-4884-b626-d94470ef9dc2",
                "content_hash": "b" * 64,
                "prompt_version": "prompt-v1",
                "model_version": "model-v1",
                "schema_version": "schema-v1",
                "lease_token": "l" * 43,
                "output": {
                    "relevance": True,
                    "sentiment": "neutral",
                    "topics": ["rates"],
                },
            },
        )

    # Then
    assert lease.status_code == 200
    assert lease.json() == {
        "outcome": "blocked_capability",
        "reason_codes": ["zero_tools_unproven"],
    }
    assert "lease_token" not in lease.text
    assert ack.status_code == 204
    assert worker.ack_payload is not None
    assert isinstance(worker.ack_payload.output, AnalysisOutput)
    assert scopes.calls == [
        ("worker-token", Scope.WORKER_LEASE),
        ("worker-token", Scope.WORKER_ACK),
    ]
