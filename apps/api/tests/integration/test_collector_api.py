from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest
from app.collection.base import CollectionError, CollectionErrorCode
from app.collection.cli import ControlPlaneClient
from app.collection.cli_config import CliError
from app.collection.page_commit import PageCommitRequest
from app.collection.repository import claim_authorization_statement
from app.core.principals import Scope
from sqlalchemy.dialects import postgresql
from tests.integration.collector_route_fixtures import (
    Authorizer,
    PageRepository,
    app_for,
    page_body,
)
from tests.integration.collector_test_constants import (
    COMMIT_ID,
    RUN_ID,
    SOURCE_ID,
)


@pytest.mark.asyncio
async def test_page_commit_returns_201_then_byte_equal_200_replay() -> None:
    # Given: one authenticated collector and an idempotent repository receipt.
    authorizer = Authorizer()
    repository = PageRepository()

    # When: response recovery submits the same page twice.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app_for(authorizer, repository)),
        base_url="https://api.test",
    ) as client:
        first = await client.post(
            f"/v1/collector/runs/{RUN_ID}/pages",
            headers={"Authorization": "Bearer collector-token"},
            json=page_body(),
        )
        replay = await client.post(
            f"/v1/collector/runs/{RUN_ID}/pages",
            headers={"Authorization": "Bearer collector-token"},
            json=page_body(),
        )

    # Then: creation and replay statuses differ while the stored body is identical.
    assert first.status_code == 201
    assert replay.status_code == 200
    assert first.content == replay.content
    assert authorizer.calls == [
        Scope.COLLECTOR_PAGE_COMMIT,
        Scope.COLLECTOR_PAGE_COMMIT,
    ]


@pytest.mark.asyncio
async def test_page_commit_maps_auth_conflict_and_contract_statuses_exactly() -> None:
    # Given: the protected page endpoint and a repository CAS conflict.
    authorizer = Authorizer()
    app = app_for(authorizer, PageRepository(conflict=True))

    # When: callers omit auth, use a wrong scope, conflict, or send a bad contract.
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        unauthorized = await client.post(
            f"/v1/collector/runs/{RUN_ID}/pages", json=page_body()
        )
        forbidden = await client.post(
            f"/v1/collector/runs/{RUN_ID}/pages",
            headers={"Authorization": "Bearer wrong-scope"},
            json=page_body(),
        )
        conflict = await client.post(
            f"/v1/collector/runs/{RUN_ID}/pages",
            headers={"Authorization": "Bearer collector-token"},
            json=page_body(),
        )
        invalid_body = page_body() | {"terminal_reason": None}
        invalid = await client.post(
            f"/v1/collector/runs/{RUN_ID}/pages",
            headers={"Authorization": "Bearer collector-token"},
            json=invalid_body,
        )

    # Then: the boundary exposes only the specified status/code contract.
    assert unauthorized.status_code == 401
    assert forbidden.status_code == 403
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "checkpoint_conflict"
    assert conflict.json()["error"]["current_checkpoint_revision"] == 8
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_control_plane_client_translates_page_conflict_body() -> None:
    # Given: the real HTTP client receives the collector route's 409 envelope.
    async def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/oidc":
            return httpx2.Response(200, json={"value": "github-token"})
        if request.url.path == "/v1/service-tokens/github/exchange":
            assert request.headers["content-type"] == "application/json"
            return httpx2.Response(200, json={"access_token": "service-token"})
        return httpx2.Response(
            409,
            json={
                "error": {
                    "code": "ordinal_gap",
                    "correlation_id": "corr-1",
                    "current_checkpoint_revision": 3,
                    "current_cursor": "cursor-2",
                    "expected_page_ordinal": 2,
                    "existing_commit_id": str(COMMIT_ID),
                }
            },
        )

    environment = {
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://github.example/oidc",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
    }
    transport = httpx2.MockTransport(respond)

    # When: commit_page crosses the public ControlPlaneClient boundary.
    async with ControlPlaneClient(
        "https://api.example", environment, transport=transport
    ) as client:
        await client.authenticate()
        with pytest.raises(CollectionError) as raised:
            _ = await client.commit_page(
                RUN_ID, PageCommitRequest.model_validate(page_body())
            )

    # Then: workflow code receives typed recovery facts, not HTTPStatusError.
    assert raised.value.code is CollectionErrorCode.ORDINAL_GAP
    assert raised.value.current_checkpoint_revision == 3
    assert raised.value.current_cursor == "cursor-2"
    assert raised.value.expected_page_ordinal == 2
    assert raised.value.existing_commit_id == COMMIT_ID


@pytest.mark.asyncio
async def test_control_plane_client_reports_only_redacted_server_error_code() -> None:
    async def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/oidc":
            return httpx2.Response(200, json={"value": "github-token"})
        if request.url.path == "/v1/service-tokens/github/exchange":
            return httpx2.Response(200, json={"access_token": "service-token"})
        return httpx2.Response(
            503,
            json={
                "error": {
                    "code": "service_unavailable",
                    "message": "service unavailable",
                    "correlation_id": "7f9b2e33-dbeb-494a-a1dd-8f3abe89e245",
                }
            },
        )

    environment = {
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://github.example/oidc",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "request-token",
    }

    async with ControlPlaneClient(
        "https://api.example",
        environment,
        transport=httpx2.MockTransport(respond),
    ) as client:
        await client.authenticate()
        with pytest.raises(
            CliError,
            match="control_plane_unavailable:http_503:service_unavailable",
        ):
            _ = await client.materialize("scope-v1", datetime.now(UTC))


def test_claim_locks_exact_authorization_rows_in_postgresql() -> None:
    # Given: a claim authorization query for one exact source and scope.
    statement = claim_authorization_statement((SOURCE_ID,), "scope-v1")

    # When: the statement is compiled for the production database dialect.
    sql = str(statement.compile(dialect=postgresql.dialect()))

    # Then: both source and decision rows are locked before claim mutation.
    assert "FOR UPDATE OF community_sources, source_authorization_decisions" in sql
    assert "community_sources.scope_version" in sql
