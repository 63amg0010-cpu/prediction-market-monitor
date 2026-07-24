from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import httpx2
import pytest
from app.api.routes import (
    commands as command_routes,
)
from app.api.routes import (
    verification as verification_routes,
)
from app.core.errors import install_error_handlers
from app.core.principals import PrincipalId, Scope
from app.domain.enums import Country, VerificationStatus
from app.services.dashboard.models import AuthorizedService, OutcomeStatus
from fastapi import FastAPI

if TYPE_CHECKING:
    from pydantic import SecretStr

NOW = datetime(2026, 7, 22, 1, 0, tzinfo=UTC)
SOURCE_ID = UUID("4368f7f2-21bd-4b3a-b26e-15036523cb6d")
SNAPSHOT_ID = UUID("23683ce1-d23e-481d-8e6b-2555933b2673")
COMMAND_ID = UUID("57c665ec-bdf0-4895-996b-646226807ba0")


class _ScopeAuthorizer:
    calls: list[tuple[str, Scope]]

    def __init__(self) -> None:
        self.calls = []

    async def authorize(
        self, token: SecretStr, required_scope: Scope
    ) -> AuthorizedService:
        self.calls.append((token.get_secret_value(), required_scope))
        return AuthorizedService(
            principal_id=PrincipalId("service:caller"),
            scopes=frozenset({required_scope}),
        )


class _AdminAuthorizer:
    context: command_routes.AdminCommandContext | None = None

    async def authorize(self, context: command_routes.AdminCommandContext) -> None:
        self.context = context


class _AdminCommands:
    retry: command_routes.CollectionRetryCommand | None = None
    reconcile: command_routes.DailyReconcileCommand | None = None

    async def retry_collection(
        self, command: command_routes.CollectionRetryCommand
    ) -> command_routes.CommandAccepted:
        self.retry = command
        return command_routes.CommandAccepted(command_id=COMMAND_ID, created=True)

    async def reconcile_daily(
        self, command: command_routes.DailyReconcileCommand
    ) -> command_routes.CommandAccepted:
        self.reconcile = command
        return command_routes.CommandAccepted(command_id=COMMAND_ID, created=False)


class _Verification:
    observation: verification_routes.VerificationObservationPayload | None = None

    async def snapshot(self) -> verification_routes.VerificationSnapshot:
        return verification_routes.VerificationSnapshot(
            snapshot_id=SNAPSHOT_ID,
            scope_version="scope-v1",
            published_at=NOW,
            checksum="a" * 64,
            sources=(
                verification_routes.VerificationSourceSnapshot(
                    source_id=SOURCE_ID,
                    country=Country.US,
                    enabled=True,
                    status=OutcomeStatus.UNKNOWN,
                    latest_successful_run_id=None,
                    latest_successful_run_finished_at=None,
                    collection_recency_seconds=None,
                    visible_publication_manifest_id=None,
                    visible_publication_sequence=None,
                    publication_first_visible_at=None,
                ),
            ),
        )

    async def record(
        self, payload: verification_routes.VerificationObservationPayload
    ) -> verification_routes.ObservationAccepted:
        self.observation = payload
        return verification_routes.ObservationAccepted(
            expected_slot_utc=payload.expected_slot_utc,
            accepted_source_count=len(payload.source_results),
        )


def _app(
    scopes: _ScopeAuthorizer,
    admin_auth: _AdminAuthorizer,
    commands: _AdminCommands,
    verification: _Verification,
) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(command_routes.create_commands_router(admin_auth, commands))
    app.include_router(
        verification_routes.create_verification_router(scopes, verification)
    )
    return app


def _client(app: FastAPI) -> httpx2.AsyncClient:
    transport = httpx2.ASGITransport(app=app)
    return httpx2.AsyncClient(transport=transport, base_url="https://api.test")


@pytest.mark.asyncio
async def test_admin_retry_requires_session_csrf_and_returns_202() -> None:
    # Given
    scopes = _ScopeAuthorizer()
    admin_auth = _AdminAuthorizer()
    commands = _AdminCommands()
    app = _app(scopes, admin_auth, commands, _Verification())

    # When
    async with _client(app) as client:
        response = await client.post(
            "/v1/commands/collection-retry",
            headers={
                "Authorization": "Bearer admin-token",
                "X-Admin-Session": "session-token",
                "X-CSRF-Token": "csrf-token",
                "Origin": "https://dashboard.test",
            },
            json={
                "request_id": "ad11640b-4b53-4876-bb01-d846a2e54412",
                "source_ids": [str(SOURCE_ID)],
                "reason": "operator_retry",
            },
        )

    # Then
    assert response.status_code == 202
    assert admin_auth.context is not None
    assert admin_auth.context.bff_token.get_secret_value() == "admin-token"
    assert admin_auth.context.session_token.get_secret_value() == "session-token"
    assert admin_auth.context.csrf_token == "csrf-token"  # noqa: S105
    assert commands.retry is not None
    assert commands.retry.source_ids == (SOURCE_ID,)


@pytest.mark.asyncio
async def test_duplicate_daily_reconcile_returns_existing_200() -> None:
    # Given
    scopes = _ScopeAuthorizer()
    commands = _AdminCommands()
    app = _app(scopes, _AdminAuthorizer(), commands, _Verification())

    # When
    async with _client(app) as client:
        response = await client.post(
            "/v1/admin/daily-reconcile",
            headers={
                "Authorization": "Bearer admin-token",
                "X-Admin-Session": "session-token",
                "X-CSRF-Token": "csrf-token",
                "Origin": "https://dashboard.test",
            },
            json={"request_id": "ad11640b-4b53-4876-bb01-d846a2e54412"},
        )

    # Then
    assert response.status_code == 200
    assert response.json() == {"command_id": str(COMMAND_ID), "created": False}


@pytest.mark.asyncio
async def test_verifier_snapshot_is_no_store_and_observation_is_201() -> None:
    # Given
    scopes = _ScopeAuthorizer()
    verification = _Verification()
    app = _app(scopes, _AdminAuthorizer(), _AdminCommands(), verification)

    # When
    async with _client(app) as client:
        snapshot = await client.get(
            "/v1/verification/snapshot",
            headers={"Authorization": "Bearer verifier-token"},
        )
        observation = await client.post(
            "/v1/verification/observations",
            headers={"Authorization": "Bearer verifier-token"},
            json={
                "scope_version": "scope-v1",
                "expected_slot_utc": "2026-07-22T00:45:00Z",
                "action_started_at": "2026-07-22T00:46:00Z",
                "snapshot_id": str(SNAPSHOT_ID),
                "snapshot_checksum": "a" * 64,
                "source_results": [
                    {
                        "source_id": str(SOURCE_ID),
                        "scheduler_latency_seconds": 60,
                        "collection_recency_seconds": None,
                        "publication_latency_seconds": None,
                        "status": VerificationStatus.FAILED,
                        "failure_code": "no_successful_run",
                    }
                ],
            },
        )

    # Then
    assert snapshot.status_code == 200
    assert snapshot.headers["cache-control"] == "no-store"
    assert observation.status_code == 201
    assert scopes.calls == [
        ("verifier-token", Scope.VERIFY_READ),
        ("verifier-token", Scope.VERIFY_WRITE),
    ]
    assert verification.observation is not None
    assert verification.observation.source_results[0].collection_recency_seconds is None
