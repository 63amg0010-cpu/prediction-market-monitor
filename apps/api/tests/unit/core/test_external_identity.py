# ruff: noqa: INP001
from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest
from app.core.errors import IdentityError, IdentityErrorCode
from app.core.principals import CredentialVersion, Scope
from app.services.identity.cron import CronCredentialVerifier
from app.services.identity.github import (
    GitHubClaimPolicy,
    GitHubOIDCClaims,
    GitHubWorkflowRule,
)
from app.services.identity.windows import (
    WorkerBootstrapRequest,
    WorkerBootstrapVerifier,
)
from pydantic import SecretBytes, SecretStr


def _github_claims() -> GitHubOIDCClaims:
    return GitHubOIDCClaims.model_validate(
        {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "monitor-control",
            "sub": "repo:owner@1/monitor@2:environment:production",
            "repository": "owner/monitor",
            "repository_id": "2",
            "repository_owner_id": "1",
            "workflow_ref": (
                "owner/monitor/.github/workflows/collect.yml@refs/heads/main"
            ),
            "ref": "refs/heads/main",
            "sha": "a" * 40,
            "environment": "production",
            "run_id": "12345",
            "run_attempt": "1",
            "jti": "github-jti-1",
            "iat": 1784606400,
            "nbf": 1784606400,
            "exp": 1784607000,
        }
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repository", "attacker/fork"),
        (
            "workflow_ref",
            "owner/monitor/.github/workflows/evil.yml@refs/heads/main",
        ),
        ("git_ref", "refs/heads/untrusted"),
        ("environment", "preview"),
    ],
)
def test_github_policy_rejects_wrong_repository_workflow_ref_or_environment(
    field: str, value: str
) -> None:
    # Given
    policy = GitHubClaimPolicy(
        repository="owner/monitor",
        workflows=(
            GitHubWorkflowRule(
                workflow_ref=(
                    "owner/monitor/.github/workflows/collect.yml@refs/heads/main"
                ),
                git_ref="refs/heads/main",
                environment="production",
                scopes=frozenset({Scope.COLLECTOR_PAGE_COMMIT}),
            ),
        ),
    )
    claims = _github_claims().model_copy(update={field: value})

    # When / Then
    with pytest.raises(IdentityError) as raised:
        _ = policy.validate_verified_claims(
            claims, datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
        )
    assert raised.value.code is IdentityErrorCode.INVALID_OIDC_CLAIMS


def test_worker_hmac_rejects_stale_request() -> None:
    # Given
    secret = b"w" * 32
    now = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    stale_at = now - timedelta(seconds=121)
    unsigned = WorkerBootstrapRequest(
        worker_id="desktop-1",
        capability_proof_id="proof-1",
        timestamp=stale_at,
        nonce="nonce-1",
        credential_version=CredentialVersion("worker-v1"),
        signature="0" * 64,
    )
    signature = hmac.new(secret, unsigned.signing_payload(), hashlib.sha256).hexdigest()
    request = unsigned.model_copy(update={"signature": signature})
    verifier = WorkerBootstrapVerifier(
        secrets={CredentialVersion("worker-v1"): SecretBytes(secret)}
    )

    # When / Then
    with pytest.raises(IdentityError) as raised:
        _ = verifier.verify(request, now)
    assert raised.value.code is IdentityErrorCode.STALE_REQUEST


def test_cron_secret_uses_exact_bearer_contract() -> None:
    # Given
    verifier = CronCredentialVerifier(SecretStr("c" * 32))

    # When / Then
    verifier.verify("Bearer " + "c" * 32)
    with pytest.raises(IdentityError) as raised:
        verifier.verify("Bearer " + "x" * 32)
    assert raised.value.code is IdentityErrorCode.INVALID_CREDENTIAL
