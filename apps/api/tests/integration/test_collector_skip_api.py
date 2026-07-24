from __future__ import annotations

import httpx2
import pytest
from tests.integration.collector_route_fixtures import (
    Authorizer,
    PageRepository,
    app_for,
)
from tests.integration.collector_test_constants import COMMAND_ID, RUN_ID


@pytest.mark.asyncio
async def test_zero_commit_provider_policy_observation_gets_server_skip_proof() -> None:
    # Given
    repository = PageRepository()
    app = app_for(Authorizer(), repository)
    payload = {
        "command_id": str(COMMAND_ID),
        "attempt": 1,
        "lease_token": "l" * 43,
        "idempotency_key": "21fd270e-1fe8-48df-b5a2-a196b92e6112",
        "provider": "reddit",
        "route": "/r/Polymarket+Kalshi+PredictionMarkets/new",
        "http_status": 401,
        "failure_code": "provider_authorization_rejected",
    }

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        response = await client.post(
            f"/v1/collector/runs/{RUN_ID}/skip-decision",
            headers={"Authorization": "Bearer collector-token"},
            json=payload,
        )

    # Then
    assert response.status_code == 201
    assert response.json()["terminal_status"] == "skipped_policy"
    assert repository.skip_calls == 1


@pytest.mark.asyncio
async def test_skip_route_rejects_client_owned_decision_fields() -> None:
    # Given: a client observation that tries to choose the server decision.
    repository = PageRepository()
    app = app_for(Authorizer(), repository)
    payload = {
        "command_id": str(COMMAND_ID),
        "attempt": 1,
        "lease_token": "l" * 43,
        "idempotency_key": "21fd270e-1fe8-48df-b5a2-a196b92e6112",
        "provider": "reddit",
        "route": "/r/Polymarket+Kalshi+PredictionMarkets/new",
        "http_status": 401,
        "failure_code": "provider_authorization_rejected",
        "decision_kind": "policy",
        "skip_decision_id": "21fd270e-1fe8-48df-b5a2-a196b92e6113",
        "terminal_status": "skipped_policy",
    }

    # When: the request crosses the public route boundary.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        response = await client.post(
            f"/v1/collector/runs/{RUN_ID}/skip-decision",
            headers={"Authorization": "Bearer collector-token"},
            json=payload,
        )

    # Then: validation rejects it before persistence or finalization.
    assert response.status_code == 422
    assert repository.skip_calls == 0
