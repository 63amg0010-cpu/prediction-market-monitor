from __future__ import annotations

import re
from pathlib import Path

import yaml
from app.api.routes.health import HealthResponse
from app.core.settings import IdentitySettings
from app.domain.types import JsonValue
from app.services.dashboard.models import DatabaseStatus
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[4]
YAML_ADAPTER = TypeAdapter(dict[str, JsonValue])


def _yaml(relative: str) -> dict[str, JsonValue]:
    source = (ROOT / relative).read_text(encoding="utf-8")
    return YAML_ADAPTER.validate_python(yaml.safe_load(source))


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_compose_supplies_complete_server_only_identity_environment() -> None:
    # Given: API and web production configuration boundaries.
    compose = _yaml("docker-compose.yml")
    services = _mapping(compose["services"])
    api = _mapping(services["api"])
    web = _mapping(services["web"])
    api_env = _mapping(api["environment"])
    web_env = _mapping(web["environment"])

    # When/Then: Compose supplies every required server-side key without
    # exposing API-only secrets through browser-visible variables.
    api_required = {
        "DATABASE_URL",
        *(field.upper() for field in IdentitySettings.model_fields),
    }
    web_required = {
        "API_BASE_URL",
        "BFF_CLIENT_CREDENTIAL",
        "BFF_CREDENTIAL_VERSION",
        "VERCEL_DEPLOYMENT_ID",
        "WEB_PUBLIC_ORIGIN",
    }
    assert api_required <= set(api_env)
    assert web_required <= set(web_env)
    assert not any(name.startswith("NEXT_PUBLIC_") for name in web_env)
    assert {
        "ADMIN_PASSWORD_ARGON2ID_HASH",
        "CRON_SECRET",
        "SERVICE_TOKEN_ISSUER_PRIVATE_KEY",
        "SESSION_HMAC_SECRET",
        "WORKER_BOOTSTRAP_SECRET",
    }.isdisjoint(web_env)
    api_healthcheck = yaml.safe_dump(api["healthcheck"])
    assert "'status'" in api_healthcheck
    assert "'db'" in api_healthcheck
    assert "'ok'" in api_healthcheck


def test_api_surfaces_supply_actual_composition_environment() -> None:
    # Given: mandatory keys declared by identity settings and the composition root.
    dependencies = _read("apps/api/app/wiring/dependencies.py")
    composition_keys = set(
        re.findall(r'environment\.get\("([A-Z][A-Z0-9_]*)"\)', dependencies)
    )
    required = {
        "DATABASE_URL",
        *(field.upper() for field in IdentitySettings.model_fields),
        *composition_keys,
    }

    # When/Then: every local and cloud API configuration surface supplies the
    # production requirements, while the Windows validator enforces the same set.
    compose = _yaml("docker-compose.yml")
    services = _mapping(compose["services"])
    api_env = set(_mapping(_mapping(services["api"])["environment"]))
    handoff = _yaml("docs/cloud-deployment-handoff.yml")
    vercel = _mapping(handoff["vercel"])
    api_keys = vercel["api_environment_keys"]
    assert isinstance(api_keys, list)
    vercel_api_env = {str(key) for key in api_keys}
    env_example = {
        line.split("=", 1)[0]
        for line in _read(".env.example").splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    setup = _read("scripts/windows/Verify-LocalSetup.ps1")
    required_block = re.search(
        r"\$requiredKeys = @\((.*?)\n\s*\)",
        setup,
        flags=re.DOTALL,
    )
    assert required_block is not None
    windows_required = set(re.findall(r'"([A-Z][A-Z0-9_]*)"', required_block.group(1)))
    missing = {
        "compose_api": required - api_env,
        "vercel_api": required - vercel_api_env,
        "env_example": required - env_example,
        "windows_validator": required - windows_required,
    }
    assert missing == {
        "compose_api": set(),
        "vercel_api": set(),
        "env_example": set(),
        "windows_validator": set(),
    }


def test_vercel_routes_all_fastapi_surfaces_to_the_python_function() -> None:
    # Given: Vercel's API project routing table.
    config = _yaml("apps/api/vercel.json")
    routes = config["routes"]
    assert isinstance(routes, list)

    # When/Then: actual API, cron, and OpenAPI paths resolve to the ASGI entrypoint.
    for path in (
        "/internal/release/workflow-dispatch-claim",
        "/v1/health",
        "/v1/dashboard",
        "/api/cron/daily",
        "/openapi.json",
        "/docs",
    ):
        matches = tuple(
            route
            for value in routes
            if isinstance(value, dict)
            for route in (_mapping(value),)
            if re.fullmatch(str(route["src"]), path)
        )
        assert any(route["dest"] == "api/index.py" for route in matches), path


def test_cloud_health_observables_match_the_public_health_model() -> None:
    # Given: the beginner handoff's machine-readable health observables.
    contract = _yaml("docs/cloud-deployment-handoff.yml")
    health = _mapping(contract["health"])

    # When/Then: healthy and degraded probes both remain HTTP 200 and use the
    # exact redacted response values emitted by the public health route.
    healthy = HealthResponse(
        status="ok",
        version="0.1.0",
        db=DatabaseStatus.OK,
    )
    degraded = HealthResponse(
        status="degraded",
        version="0.1.0",
        db=DatabaseStatus.UNAVAILABLE,
    )
    assert _mapping(health["healthy"]) == {
        "http_status": 200,
        "body": healthy.model_dump(mode="json"),
    }
    assert _mapping(health["degraded"]) == {
        "http_status": 200,
        "body": degraded.model_dump(mode="json"),
    }
    assert health["protected_unconfigured_status"] == 503
