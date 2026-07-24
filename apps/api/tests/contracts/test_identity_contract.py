from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest
from app.api.routes.service_tokens import create_service_token_router
from app.core.errors import install_error_handlers
from app.core.principals import Scope
from app.services.identity.exchanges import (
    BffExchangeCommand,
    BffExchangeResponse,
    GitHubExchangeCommand,
    WorkerExchangeCommand,
)
from fastapi import FastAPI

_ISSUED_VALUE = "issued-ed25519-jwt"


class _BffExchangeHandler:
    async def exchange_bff(self, command: BffExchangeCommand) -> BffExchangeResponse:
        del command
        return self._response()

    async def exchange_github(
        self, command: GitHubExchangeCommand
    ) -> BffExchangeResponse:
        del command
        return self._response()

    async def exchange_worker(
        self, command: WorkerExchangeCommand
    ) -> BffExchangeResponse:
        del command
        return self._response()

    @staticmethod
    def _response() -> BffExchangeResponse:
        return BffExchangeResponse(
            access_token=_ISSUED_VALUE,
            expires_at=datetime(2026, 7, 21, 4, 5, tzinfo=UTC),
            scope=(Scope.BFF_AUTH, Scope.BFF_READ),
        )


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(create_service_token_router(_BffExchangeHandler()))
    return app


@pytest.mark.asyncio
async def test_bff_exchange_contract_is_server_only_and_no_store() -> None:
    # Given
    credential = "server-only-bff-credential"

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_app()), base_url="https://api.test"
    ) as client:
        response = await client.post(
            "/v1/service-tokens/bff/exchange",
            headers={
                "Authorization": f"Bearer {credential}",
                "X-Deployment-Identity": "vercel-production",
                "X-Correlation-ID": "d943e39b-e76b-4b83-b387-0787abeec565",
            },
            json={
                "credential_version": "bff-v1",
                "request_nonce": "8e256a5b-01c3-4a6c-9983-66a7164cf3d8",
                "requested_scopes": ["bff:auth", "bff:read"],
            },
        )

    # Then
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert (
        response.headers["x-correlation-id"] == "d943e39b-e76b-4b83-b387-0787abeec565"
    )
    assert response.json() == {
        "access_token": _ISSUED_VALUE,
        "token_type": "Bearer",
        "expires_at": "2026-07-21T04:05:00Z",
        "scope": ["bff:auth", "bff:read"],
    }
    assert credential not in response.text


@pytest.mark.asyncio
async def test_malformed_secret_input_uses_redacted_typed_error_envelope() -> None:
    # Given
    malformed_version = "must-not-be-echoed-by-validation"

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=_app()), base_url="https://api.test"
    ) as client:
        response = await client.post(
            "/v1/service-tokens/bff/exchange",
            headers={
                "Authorization": "Bearer x",
                "X-Deployment-Identity": "deploy",
            },
            json={
                "credential_version": malformed_version,
                "request_nonce": "not-a-uuid",
                "requested_scopes": ["invalid:scope"],
            },
        )

    # Then
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert malformed_version not in response.text
