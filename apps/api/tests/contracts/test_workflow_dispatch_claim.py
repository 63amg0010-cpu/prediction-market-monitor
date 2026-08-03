from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.api.routes.workflow_dispatch_claim import (
    CLAIM_RESERVATION_SQL,
    WorkflowDispatchClaimOidcAuthorizer,
)
from app.core.errors import IdentityError
from app.main import AppDependencies, create_app
from app.services.identity.github import GitHubOIDCClaims
from app.services.release.workflow_claims import WorkflowDispatchClaimRequest
from fastapi.testclient import TestClient
from pydantic import SecretStr

NOW = datetime(2026, 7, 29, 4, tzinfo=UTC)
REPOSITORY = "63amg0010-cpu/prediction-market-monitor"
WORKFLOW = "ci.yml"


@dataclass(frozen=True, slots=True)
class _Clock:
    def now(self) -> datetime:
        return NOW


class _Verifier:
    def __init__(self, claims: GitHubOIDCClaims) -> None:
        self.claims: GitHubOIDCClaims = claims

    async def verify(
        self, token: SecretStr, now: datetime
    ) -> GitHubOIDCClaims:
        assert token.get_secret_value() == "oidc"
        assert now == NOW
        return self.claims


def _request() -> WorkflowDispatchClaimRequest:
    return WorkflowDispatchClaimRequest(
        repository=REPOSITORY,
        workflow=WORKFLOW,
        display_title="ci-22222222-2222-4222-8222-222222222222-attempt-3",
        head_sha="a" * 40,
        approved_plan_sha256="b" * 64,
        activation_nonce=UUID("11111111-1111-4111-8111-111111111111"),
        dispatch_nonce=UUID("22222222-2222-4222-8222-222222222222"),
        reservation_sha256="c" * 64,
        run_id=123,
        run_attempt=3,
        event="workflow_dispatch",
        ref="refs/heads/main",
        environment="production-collector",
    )


def _claims() -> GitHubOIDCClaims:
    timestamp = int(NOW.timestamp())
    return GitHubOIDCClaims.model_validate(
        {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "monitor-control",
            "sub": (
                "repo:63amg0010-cpu@256795069/"
                "prediction-market-monitor@1310655558:"
                "environment:production-collector"
            ),
            "repository": REPOSITORY,
            "repository_id": "1310655558",
            "repository_owner_id": "256795069",
            "workflow_ref": (
                f"{REPOSITORY}/.github/workflows/{WORKFLOW}@refs/heads/main"
            ),
            "ref": "refs/heads/main",
            "sha": "a" * 40,
            "environment": "production-collector",
            "run_id": "123",
            "run_attempt": "3",
            "jti": "claim-jti",
            "iat": timestamp,
            "nbf": timestamp,
            "exp": timestamp + 300,
        }
    )


def test_workflow_dispatch_claim_route_is_schema_closed_and_registered() -> None:
    with TestClient(create_app(AppDependencies())) as client:
        response = client.post(
            "/internal/release/workflow-dispatch-claim",
            headers={"Authorization": "Bearer invalid"},
            json={**_request().model_dump(mode="json"), "database_url": "forbidden"},
        )

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_claim_oidc_binds_exact_workflow_run_identity() -> None:
    authorizer = WorkflowDispatchClaimOidcAuthorizer(
        verifier=_Verifier(_claims()),
        clock=_Clock(),
        repository=REPOSITORY,
    )

    claims = await authorizer.authorize(SecretStr("oidc"), _request())

    assert claims.run_id == "123"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "foreign/repository"),
        ("repository_id", "999"),
        ("repository_owner_id", "998"),
        ("workflow_ref", "foreign/repository/.github/workflows/ci.yml@main"),
        ("git_ref", "refs/heads/release"),
        ("head_sha", "f" * 40),
        ("run_id", "124"),
        ("run_attempt", "4"),
        ("environment", "production"),
    ],
)
@pytest.mark.asyncio
async def test_claim_oidc_rejects_every_identity_drift(
    field: str, value: str
) -> None:
    authorizer = WorkflowDispatchClaimOidcAuthorizer(
        verifier=_Verifier(_claims().model_copy(update={field: value})),
        clock=_Clock(),
        repository=REPOSITORY,
    )

    with pytest.raises(IdentityError):
        _ = await authorizer.authorize(SecretStr("oidc"), _request())


def test_atomic_claim_sql_binds_every_request_identity() -> None:
    assert CLAIM_RESERVATION_SQL.lstrip().startswith("UPDATE")
    for field in (
        "repository",
        "workflow_file",
        "event_name",
        "display_title",
        "reviewed_sha",
        "approved_plan_sha256",
        "activation_nonce",
        "dispatch_nonce",
        "receipt_sha256",
        "attempt",
        "claimed_run_id IS NULL",
    ):
        assert field in CLAIM_RESERVATION_SQL
