# ruff: noqa: INP001
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Never

import httpx2
import pytest
from app.core.errors import IdentityError
from app.core.jwt import Ed25519TokenIssuer, Ed25519TokenVerifier
from app.core.principals import CredentialVersion, PrincipalId, PrincipalKind, Scope
from app.services.dashboard.sql_authorizer import SqlScopeAuthorizer
from app.services.identity.bff import BffAuthorization
from app.services.identity.exchanges import (
    BffExchangeCommand,
    GitHubExchangeCommand,
    IdentityExchangeDependencies,
    IdentityExchangePolicy,
    IdentityExchangeService,
)
from app.services.identity.github import (
    GitHubClaimPolicy,
    GitHubExchangeAuthorizer,
    GitHubOIDCClaims,
    GitHubWorkflowRule,
)
from app.services.identity.ports import (
    PrincipalAuthorizationDecision,
    PrincipalAuthorizationRequest,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import SecretStr
from tests.integration.collector_route_fixtures import (
    PageRepository,
    app_for,
    page_body,
)
from tests.integration.collector_test_constants import RUN_ID

if TYPE_CHECKING:
    from app.services.identity.bff import BffExchangeRequest
    from app.services.identity.ports import GitHubPrincipalRegistration
    from app.services.identity.windows import WorkerBootstrapRequest


class _Clock:
    def __init__(self, now: datetime) -> None:
        self._now: datetime = now

    def now(self) -> datetime:
        return self._now


class _OidcVerifier:
    def __init__(self, claims: GitHubOIDCClaims) -> None:
        self._claims: GitHubOIDCClaims = claims

    async def verify(self, token: SecretStr, now: datetime) -> GitHubOIDCClaims:
        del token, now
        return self._claims


class _Nonces:
    async def consume_once(
        self, namespace: str, key: str, retain_until: datetime
    ) -> bool:
        del namespace, key, retain_until
        return True


class _PrincipalRegistry:
    def __init__(self) -> None:
        self.registrations: list[GitHubPrincipalRegistration] = []

    async def register(self, request: GitHubPrincipalRegistration) -> bool:
        self.registrations.append(request)
        return True

    async def authorize(
        self, request: PrincipalAuthorizationRequest
    ) -> PrincipalAuthorizationDecision:
        authorized = any(
            item.principal_id == request.principal_id
            and item.credential_version == request.credential_version
            and item.valid_from <= request.checked_at < item.valid_until
            for item in self.registrations
        )
        return PrincipalAuthorizationDecision(authorized)


class _UnusedBff:
    async def authorize(self, request: BffExchangeRequest, now: datetime) -> Never:
        del request, now
        raise AssertionError


class _Bff:
    async def authorize(
        self, request: BffExchangeRequest, now: datetime
    ) -> BffAuthorization:
        del now
        return BffAuthorization(
            principal_id=PrincipalId(f"bff:{request.deployment_identity}"),
            credential_version=request.credential_version,
            scopes=request.requested_scopes,
        )


class _UnusedWorker:
    async def authorize(self, request: WorkerBootstrapRequest, now: datetime) -> Never:
        del request, now
        raise AssertionError


def _claims(now: datetime, *, repository: str = "owner/monitor") -> GitHubOIDCClaims:
    return GitHubOIDCClaims.model_validate(
        {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "monitor-control",
            "sub": "repo:owner/monitor:environment:production",
            "repository": repository,
            "job_workflow_ref": (
                "owner/monitor/.github/workflows/collect.yml@refs/heads/main"
            ),
            "ref": "refs/heads/main",
            "sha": "a" * 40,
            "environment": "production",
            "run_id": "12345",
            "run_attempt": "1",
            "jti": "github-jti-1",
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
    )


def _service(
    now: datetime,
    claims: GitHubOIDCClaims,
    registry: _PrincipalRegistry,
    scopes: frozenset[Scope] | None = None,
) -> tuple[IdentityExchangeService, Ed25519TokenVerifier]:
    private_key = Ed25519PrivateKey.generate()
    granted_scopes = scopes or frozenset({Scope.COLLECTOR_MATERIALIZE})
    policy = GitHubClaimPolicy(
        "owner/monitor",
        (
            GitHubWorkflowRule(
                claims.job_workflow_ref,
                "refs/heads/main",
                "production",
                granted_scopes,
            ),
        ),
    )
    verifier = Ed25519TokenVerifier(
        issuer="monitor-api", public_keys={"key-1": private_key.public_key()}
    )
    service = IdentityExchangeService(
        IdentityExchangeDependencies(
            clock=_Clock(now),
            issuer=Ed25519TokenIssuer(
                issuer="monitor-api", active_key_id="key-1", private_key=private_key
            ),
            bff=_UnusedBff(),
            github_verifier=_OidcVerifier(claims),
            github=GitHubExchangeAuthorizer(policy, _Nonces()),
            github_principals=registry,
            worker=_UnusedWorker(),
        ),
        IdentityExchangePolicy("https://api.test", CredentialVersion("1")),
    )
    return service, verifier


@pytest.mark.asyncio
async def test_first_github_exchange_registers_authorizable_principal() -> None:
    # Given
    now = datetime.now(UTC).replace(microsecond=0)
    registry = _PrincipalRegistry()
    service, verifier = _service(now, _claims(now), registry)

    # When
    exchanged = await service.exchange_github(
        GitHubExchangeCommand(SecretStr("signed-oidc"))
    )
    authorized = await SqlScopeAuthorizer(
        verifier, registry, "https://api.test"
    ).authorize(SecretStr(exchanged.access_token), Scope.COLLECTOR_MATERIALIZE)

    # Then
    assert authorized.principal_id == PrincipalId("github:owner/monitor:12345")
    assert len(registry.registrations) == 1
    registration = registry.registrations[0]
    assert registration.kind is PrincipalKind.GITHUB_COLLECTOR
    assert registration.credential_version == CredentialVersion("1")
    assert registration.valid_until == exchanged.expires_at


@pytest.mark.asyncio
async def test_first_github_jwt_reaches_actual_scoped_collector_route() -> None:
    # Given
    now = datetime.now(UTC).replace(microsecond=0)
    registry = _PrincipalRegistry()
    service, verifier = _service(
        now,
        _claims(now),
        registry,
        frozenset({Scope.COLLECTOR_PAGE_COMMIT}),
    )
    exchanged = await service.exchange_github(
        GitHubExchangeCommand(SecretStr("signed-oidc"))
    )
    app = app_for(
        SqlScopeAuthorizer(verifier, registry, "https://api.test"), PageRepository()
    )

    # When
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="https://api.test"
    ) as client:
        response = await client.post(
            f"/v1/collector/runs/{RUN_ID}/pages",
            headers={"Authorization": f"Bearer {exchanged.access_token}"},
            json=page_body(),
        )

    # Then
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_unreviewed_github_repository_cannot_register_principal() -> None:
    # Given
    now = datetime.now(UTC).replace(microsecond=0)
    registry = _PrincipalRegistry()
    service, _ = _service(now, _claims(now, repository="attacker/fork"), registry)

    # When / Then
    with pytest.raises(IdentityError):
        _ = await service.exchange_github(GitHubExchangeCommand(SecretStr("oidc")))
    assert registry.registrations == []


@pytest.mark.asyncio
async def test_first_bff_exchange_registers_authorizable_deployment() -> None:
    # Given
    now = datetime.now(UTC).replace(microsecond=0)
    registry = _PrincipalRegistry()
    private_key = Ed25519PrivateKey.generate()
    verifier = Ed25519TokenVerifier(
        issuer="monitor-api", public_keys={"key-1": private_key.public_key()}
    )
    service = IdentityExchangeService(
        IdentityExchangeDependencies(
            clock=_Clock(now),
            issuer=Ed25519TokenIssuer(
                issuer="monitor-api", active_key_id="key-1", private_key=private_key
            ),
            bff=_Bff(),
            github_verifier=_OidcVerifier(_claims(now)),
            github=GitHubExchangeAuthorizer(
                GitHubClaimPolicy(
                    "owner/monitor",
                    (
                        GitHubWorkflowRule(
                            _claims(now).job_workflow_ref,
                            "refs/heads/main",
                            "production",
                            frozenset({Scope.COLLECTOR_MATERIALIZE}),
                        ),
                    ),
                ),
                _Nonces(),
            ),
            github_principals=registry,
            worker=_UnusedWorker(),
        ),
        IdentityExchangePolicy("https://api.test", CredentialVersion("1")),
    )

    # When
    exchanged = await service.exchange_bff(
        BffExchangeCommand(
            credential_version=CredentialVersion("bff-v1"),
            presented_credential=SecretStr("server-only-credential"),
            request_nonce="nonce-1",
            requested_scopes=frozenset({Scope.BFF_AUTH, Scope.BFF_READ}),
            deployment_identity="deployment-123",
        )
    )
    authorized = await SqlScopeAuthorizer(
        verifier, registry, "https://api.test"
    ).authorize(SecretStr(exchanged.access_token), Scope.BFF_AUTH)

    # Then
    assert authorized.principal_id == PrincipalId("bff:deployment-123")
    assert len(registry.registrations) == 1
    assert registry.registrations[0].kind is PrincipalKind.BFF
    assert registry.registrations[0].credential_version == CredentialVersion("bff-v1")
    assert registry.registrations[0].valid_until == exchanged.expires_at
