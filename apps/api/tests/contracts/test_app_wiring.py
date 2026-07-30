from __future__ import annotations

import warnings
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final

import pytest
from api.index import app as deployed_application
from app.api.routes.activation_evidence import (
    ActivationEvidenceOidcAuthorizer,
    SqlActivationEvidenceVerifier,
)
from app.api.routes.health import HealthResponse
from app.core.errors import ErrorEnvelope, IdentityErrorCode
from app.domain.types import JsonValue  # noqa: TC002 - Pydantic runtime field.
from app.main import AppDependencies, create_app
from app.openapi import write_openapi
from app.services.dashboard.models import DatabaseStatus
from app.services.identity.github_oidc import GitHubJwksOidcVerifier
from app.wiring.dependencies import dependencies_from_environment
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import StarletteDeprecationWarning

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

REQUIRED_OPERATIONS = frozenset(
    {
        ("GET", "/api/cron/daily"),
        ("GET", "/v1/auth/session"),
        ("GET", "/v1/collector/runs/{run_id}/checkpoint"),
        ("GET", "/v1/dashboard"),
        ("GET", "/v1/health"),
        ("GET", "/v1/posts"),
        ("GET", "/v1/reports"),
        ("GET", "/v1/reports/{report_date}"),
        ("GET", "/v1/verification/snapshot"),
        ("POST", "/v1/admin/daily-reconcile"),
        ("POST", "/v1/auth/login"),
        ("POST", "/v1/auth/logout"),
        ("POST", "/internal/release/activation-evidence-verify"),
        ("POST", "/internal/release/cadence-workflow-attempt"),
        ("POST", "/internal/release/workflow-dispatch-claim"),
        ("POST", "/v1/collector/commands/{command_id}/claim"),
        ("POST", "/v1/collector/commands/{command_id}/complete"),
        ("POST", "/v1/collector/commands/{command_id}/confirm-dispatch"),
        ("POST", "/v1/collector/commands/{command_id}/heartbeat"),
        ("POST", "/v1/collector/commands/{command_id}/reserve"),
        ("POST", "/v1/collector/materialize"),
        ("POST", "/v1/collector/runs/{run_id}/pages"),
        ("POST", "/v1/collector/runs/{run_id}/skip-decision"),
        ("POST", "/v1/commands/collection-retry"),
        ("POST", "/v1/service-tokens/bff/exchange"),
        ("POST", "/v1/service-tokens/github/exchange"),
        ("POST", "/v1/service-tokens/worker/exchange"),
        ("POST", "/v1/verification/observations"),
        ("POST", "/v1/worker/ack"),
        ("POST", "/v1/worker/heartbeat"),
        ("POST", "/v1/worker/lease"),
    }
)
HTTP_METHODS: Final = frozenset({"delete", "get", "patch", "post", "put"})


class _OpenApiDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    components: dict[str, dict[str, JsonValue]]
    paths: dict[str, dict[str, JsonValue]]


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _api_operations(
    application: FastAPI, openapi_target: Path
) -> tuple[tuple[str, str], ...]:
    document = _OpenApiDocument.model_validate_json(
        write_openapi(application, openapi_target)
    )
    operations: list[tuple[str, str]] = []
    for path, methods in document.paths.items():
        operations.extend(
            (method.upper(), path) for method in HTTP_METHODS if method in methods
        )
    return tuple(operations)


def _test_client(application: FastAPI) -> TestClient:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", StarletteDeprecationWarning)
        from fastapi.testclient import TestClient  # noqa: PLC0415

    return TestClient(application)


def test_deployed_app_registers_every_required_operation_once(
    tmp_path: Path,
) -> None:
    # Given
    application = deployed_application

    # When
    operations = _api_operations(application, tmp_path / "openapi.json")

    # Then
    assert set(operations) == REQUIRED_OPERATIONS
    assert len(operations) == len(set(operations))


def test_cadence_workflow_operation_is_named_and_schema_closed(
    tmp_path: Path,
) -> None:
    # Given
    openapi_target = tmp_path / "openapi.json"
    document = _OpenApiDocument.model_validate_json(
        write_openapi(deployed_application, openapi_target)
    )

    # When
    operation = _mapping(
        document.paths["/internal/release/cadence-workflow-attempt"]["post"]
    )
    request_body = _mapping(operation["requestBody"])
    request_content = _mapping(request_body["content"])
    request_media = _mapping(request_content["application/json"])
    response = _mapping(_mapping(operation["responses"])["200"])
    response_content = _mapping(response["content"])
    response_media = _mapping(response_content["application/json"])
    schemas = document.components["schemas"]
    request_schema = _mapping(schemas["CadenceWorkflowAttemptRequest"])
    receipt_schema = _mapping(schemas["CadenceWorkflowAttemptReceipt"])

    # Then
    assert (
        operation["operationId"]
        == "record_internal_release_cadence_workflow_attempt_post"
    )
    assert request_media["schema"] == {
        "$ref": "#/components/schemas/CadenceWorkflowAttemptRequest"
    }
    assert response_media["schema"] == {
        "$ref": "#/components/schemas/CadenceWorkflowAttemptReceipt"
    }
    assert request_schema["additionalProperties"] is False
    assert receipt_schema["additionalProperties"] is False


def test_collector_preserves_unauthorized_status_instead_of_404() -> None:
    # Given
    application = create_app(AppDependencies())
    run_id = "7c4ade1f-b450-46b2-aaed-cda121160d1e"

    # When
    with _test_client(application) as client:
        collector = client.get(f"/v1/collector/runs/{run_id}/checkpoint")

    # Then
    assert collector.status_code == 401
    assert collector.status_code != 404


def test_verifier_preserves_unauthorized_status_instead_of_404() -> None:
    # Given
    application = create_app(AppDependencies())

    # When
    with _test_client(application) as client:
        verifier = client.get("/v1/verification/snapshot")

    # Then
    assert verifier.status_code == 401
    assert verifier.status_code != 404


def test_missing_durable_authorizer_returns_typed_503() -> None:
    # Given
    application = create_app(AppDependencies())

    # When
    with _test_client(application) as client:
        response = client.get(
            "/v1/verification/snapshot",
            headers={"Authorization": "Bearer verifier-token"},
        )

    # Then
    assert response.status_code == 503
    envelope = ErrorEnvelope.model_validate_json(response.content)
    assert envelope.error.code is IdentityErrorCode.SERVICE_UNAVAILABLE


def test_health_uses_the_fail_closed_database_probe() -> None:
    # Given
    application = create_app(AppDependencies())

    # When
    with _test_client(application) as client:
        response = client.get("/v1/health")

    # Then
    assert response.status_code == 200
    assert HealthResponse.model_validate_json(response.content) == HealthResponse(
        status="degraded",
        version="0.1.0",
        db=DatabaseStatus.UNAVAILABLE,
    )
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_complete_production_configuration_builds_phase2_dependencies() -> None:
    # Given
    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    environment = {
        "API_BASE_URL": "https://api.example.test",
        "SERVICE_TOKEN_KEY_ID": "primary-2026-07",
        "SERVICE_TOKEN_ISSUER_PRIVATE_KEY": private_pem,
        "SERVICE_TOKEN_ISSUER_PUBLIC_KEY": public_pem,
        "BFF_CLIENT_CREDENTIAL": "bff-secret-material-not-logged-000001",
        "BFF_CREDENTIAL_VERSION": "1",
        "WORKER_BOOTSTRAP_SECRET": "worker-secret-material-not-logged-001",
        "WORKER_CREDENTIAL_VERSION": "1",
        "CRON_SECRET": "cron-secret-material-not-logged-00001",
        "ADMIN_PASSWORD_ARGON2ID_HASH": (
            "$argon2id$v=19$m=65536,t=3,p=4$"
            "c29tZXNhbHQ$RHVtbXlIYXNoVGhhdElzTG9uZ0Vub3VnaA"
        ),
        "SESSION_HMAC_SECRET": "session-secret-material-not-logged-001",
        "GITHUB_REPOSITORY": "owner/monitor",
        "GITHUB_WORKFLOW_REFS": (
            '["owner/monitor/.github/workflows/collect.yml@refs/heads/main",'
            '"owner/monitor/.github/workflows/verify.yml@refs/heads/main"]'
        ),
        "GITHUB_ALLOWED_REFS": '["refs/heads/main","refs/heads/main"]',
        "GITHUB_ALLOWED_ENVIRONMENTS": '["production","production"]',
        "DATABASE_URL": "postgresql+asyncpg://monitor:secret@localhost:5432/monitor",
        "MONITOR_SCOPE_VERSION": "scope-v1",
    }

    # When
    dependencies = dependencies_from_environment(environment)

    # Then
    assert dependencies.sessions is not None
    assert dependencies.service_token_handler is not None
    assert dependencies.scope_authorizer is not None
    assert dependencies.verification_handler is not None
    assert isinstance(
        dependencies.activation_evidence_verifier,
        SqlActivationEvidenceVerifier,
    )
    oidc = vars(dependencies.activation_evidence_verifier).get("_oidc")
    assert isinstance(oidc, ActivationEvidenceOidcAuthorizer)
    verifier = vars(oidc).get("_verifier")
    assert isinstance(verifier, GitHubJwksOidcVerifier)
    await dependencies.sessions.close()


def test_incomplete_production_configuration_builds_no_phase2_identity() -> None:
    # Given: a scope name without database or cryptographic identity prerequisites.
    environment = {"MONITOR_SCOPE_VERSION": "scope-v1"}

    # When: the production composition root evaluates the partial environment.
    dependencies = dependencies_from_environment(environment)

    # Then: no exchange, scope, or verification authority is partially enabled.
    assert dependencies.service_token_handler is None
    assert dependencies.scope_authorizer is None
    assert dependencies.verification_handler is None


def test_cron_route_rejects_a_missing_credential_before_handler_work() -> None:
    # Given
    application = create_app(AppDependencies())

    # When
    with _test_client(application) as client:
        response = client.get("/api/cron/daily")

    # Then
    assert response.status_code == 401
    assert response.status_code != 404


def test_committed_openapi_has_no_wiring_drift(tmp_path: Path) -> None:
    # Given
    application = create_app(AppDependencies())
    committed = Path(__file__).parents[2] / "openapi.json"

    # When
    generated = write_openapi(application, tmp_path / "openapi.json")

    # Then
    assert committed.read_bytes() == generated
