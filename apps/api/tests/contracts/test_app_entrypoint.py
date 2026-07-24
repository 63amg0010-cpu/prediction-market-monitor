from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2
import pytest
from app.domain.types import JsonValue
from app.main import create_app
from app.openapi import write_openapi
from pydantic import TypeAdapter

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.asyncio
async def test_health_is_redacted_and_has_a_correlation_id() -> None:
    # Given
    transport = httpx2.ASGITransport(app=create_app())

    # When
    async with httpx2.AsyncClient(
        transport=transport, base_url="https://api.test"
    ) as client:
        response = await client.get("/v1/health")

    # Then
    assert response.status_code == 200
    assert response.headers["x-correlation-id"]
    assert response.json() == {
        "status": "degraded",
        "version": "0.1.0",
        "db": "unavailable",
    }
    assert "SECRET" not in response.text
    assert "credential" not in response.text.lower()


@pytest.mark.asyncio
async def test_correlation_id_is_preserved_and_cors_is_denied_by_default() -> None:
    # Given
    transport = httpx2.ASGITransport(app=create_app())
    correlation_id = "d943e39b-e76b-4b83-b387-0787abeec565"

    # When
    async with httpx2.AsyncClient(
        transport=transport, base_url="https://api.test"
    ) as client:
        response = await client.get(
            "/v1/health",
            headers={
                "Origin": "https://untrusted.example",
                "X-Correlation-ID": correlation_id,
            },
        )

    # Then
    assert response.headers["x-correlation-id"] == correlation_id
    assert "access-control-allow-origin" not in response.headers


def test_identity_routes_are_registered_without_a_dev_bypass(tmp_path: Path) -> None:
    # Given
    app = create_app()
    document = TypeAdapter(dict[str, JsonValue]).validate_json(
        write_openapi(app, tmp_path / "openapi-contract.json")
    )
    paths_value = document["paths"]
    assert isinstance(paths_value, dict)
    paths = set(paths_value)

    # When / Then
    assert {
        "/v1/auth/login",
        "/v1/auth/session",
        "/v1/auth/logout",
        "/v1/service-tokens/bff/exchange",
        "/v1/service-tokens/github/exchange",
        "/v1/service-tokens/worker/exchange",
    } <= paths


def test_openapi_writer_is_deterministic(tmp_path: Path) -> None:
    # Given
    app = create_app()
    target = tmp_path / "openapi.json"

    # When
    first = write_openapi(app, target)
    first_bytes = target.read_bytes()
    second = write_openapi(app, target)

    # Then
    assert first == second
    assert first_bytes == target.read_bytes()
    document = TypeAdapter(dict[str, JsonValue]).validate_json(first_bytes)
    assert "paths" in document
