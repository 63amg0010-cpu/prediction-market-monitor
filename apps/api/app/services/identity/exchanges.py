"""Scoped service-token exchange orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, final

from pydantic import BaseModel, ConfigDict, SecretStr

from app.core.errors import IdentityError, IdentityErrorCode
from app.core.jwt import Ed25519TokenIssuer, IssueTokenRequest
from app.core.principals import CredentialVersion, Principal, PrincipalKind, Scope

from .bff import BFF_TOKEN_TTL_SECONDS, BffExchangeRequest
from .ports import GitHubPrincipalRegistration, GitHubPrincipalRepository
from .windows import (
    WORKER_TOKEN_TTL_SECONDS,
    WorkerBootstrapRequest,
)

if TYPE_CHECKING:
    from .bff import BffAuthorization
    from .github import GitHubAuthorization, GitHubOIDCClaims
    from .windows import VerifiedWorkerBootstrap

GITHUB_TOKEN_TTL_SECONDS = 600


class Clock(Protocol):
    """Clock port used to make token boundaries deterministic in tests."""

    def now(self) -> datetime:
        """Return an aware UTC time."""
        ...


@final
class SystemClock:
    """UTC application clock."""

    def now(self) -> datetime:
        """Return the current aware UTC time."""
        return datetime.now(UTC)


class GitHubOIDCVerifier(Protocol):
    """Cryptographically verify GitHub OIDC before claim policy evaluation."""

    async def verify(self, token: SecretStr, now: datetime) -> GitHubOIDCClaims:
        """Verify OIDC signature and return parsed claims."""
        ...


class BffAuthorizer(Protocol):
    """Authorize server-only BFF exchange credentials."""

    async def authorize(
        self, request: BffExchangeRequest, now: datetime
    ) -> BffAuthorization:
        """Return the exact authorized BFF identity and scopes."""
        ...


class GitHubAuthorizer(Protocol):
    """Authorize exact reviewed GitHub OIDC claims."""

    async def authorize(
        self, claims: GitHubOIDCClaims, now: datetime
    ) -> GitHubAuthorization:
        """Return the reviewed workflow-run authorization."""
        ...


class WorkerAuthorizer(Protocol):
    """Authorize a durable Windows worker bootstrap proof."""

    async def authorize(
        self, request: WorkerBootstrapRequest, now: datetime
    ) -> VerifiedWorkerBootstrap:
        """Return the approved worker identity and scopes."""
        ...


@dataclass(frozen=True, slots=True)
class BffExchangeCommand:
    """Trusted HTTP-boundary input for a BFF exchange."""

    credential_version: CredentialVersion
    presented_credential: SecretStr
    request_nonce: str
    requested_scopes: frozenset[Scope]
    deployment_identity: str


@dataclass(frozen=True, slots=True)
class GitHubExchangeCommand:
    """Raw OIDC token supplied to the cryptographic verifier port."""

    oidc_token: SecretStr


@dataclass(frozen=True, slots=True)
class WorkerExchangeCommand:
    """Parsed canonical Windows worker bootstrap request."""

    request: WorkerBootstrapRequest


class BffExchangeResponse(BaseModel):
    """Short-lived service token response consumed only by a server."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - OAuth token type.
    expires_at: datetime
    scope: tuple[Scope, ...]


class ServiceTokenExchangeHandler(Protocol):
    """HTTP-facing service token exchange operations."""

    async def exchange_bff(self, command: BffExchangeCommand) -> BffExchangeResponse:
        """Authorize and exchange BFF credentials."""
        ...

    async def exchange_github(
        self, command: GitHubExchangeCommand
    ) -> BffExchangeResponse:
        """Authorize and exchange GitHub OIDC credentials."""
        ...

    async def exchange_worker(
        self, command: WorkerExchangeCommand
    ) -> BffExchangeResponse:
        """Authorize and exchange Windows worker credentials."""
        ...


@dataclass(frozen=True, slots=True)
class IdentityExchangeDependencies:
    """Collaborators required by all fail-closed exchange paths."""

    clock: Clock
    issuer: Ed25519TokenIssuer
    bff: BffAuthorizer
    github_verifier: GitHubOIDCVerifier
    github: GitHubAuthorizer
    github_principals: GitHubPrincipalRepository
    worker: WorkerAuthorizer


@dataclass(frozen=True, slots=True)
class IdentityExchangePolicy:
    """Deployment-bound token audience and external credential versions."""

    audience: str
    github_credential_version: CredentialVersion


@final
class IdentityExchangeService:
    """Issue non-refreshable JWTs only after provider-specific authorization."""

    def __init__(
        self,
        dependencies: IdentityExchangeDependencies,
        policy: IdentityExchangePolicy,
    ) -> None:
        """Configure provider authorizers, issuer, and deployment policy."""
        self._dependencies = dependencies
        self._policy = policy

    async def exchange_bff(self, command: BffExchangeCommand) -> BffExchangeResponse:
        """Authorize BFF credentials and issue an exact-scope five-minute JWT."""
        now = self._dependencies.clock.now()
        authorization = await self._dependencies.bff.authorize(
            BffExchangeRequest(
                credential_version=command.credential_version,
                presented_credential=command.presented_credential,
                request_nonce=command.request_nonce,
                requested_scopes=command.requested_scopes,
                deployment_identity=command.deployment_identity,
            ),
            now,
        )
        expires_at = now + timedelta(seconds=BFF_TOKEN_TTL_SECONDS)
        registered = await self._dependencies.github_principals.register(
            GitHubPrincipalRegistration(
                principal_id=authorization.principal_id,
                kind=PrincipalKind.BFF,
                credential_version=authorization.credential_version,
                workflow_ref=f"bff-deployment:{command.deployment_identity}",
                valid_from=now,
                valid_until=expires_at,
            )
        )
        if not registered:
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "BFF deployment principal is revoked",
            )
        principal = Principal(
            id=authorization.principal_id,
            kind=PrincipalKind.BFF,
            credential_version=authorization.credential_version,
        )
        return self._issue(principal, authorization.scopes, now, BFF_TOKEN_TTL_SECONDS)

    async def exchange_github(
        self, command: GitHubExchangeCommand
    ) -> BffExchangeResponse:
        """Verify GitHub OIDC, consume replay state, and issue a ten-minute JWT."""
        now = self._dependencies.clock.now()
        claims = await self._dependencies.github_verifier.verify(
            command.oidc_token, now
        )
        authorization = await self._dependencies.github.authorize(claims, now)
        expires_at = now + timedelta(seconds=GITHUB_TOKEN_TTL_SECONDS)
        registered = await self._dependencies.github_principals.register(
            GitHubPrincipalRegistration(
                principal_id=authorization.principal_id,
                kind=authorization.kind,
                credential_version=self._policy.github_credential_version,
                workflow_ref=claims.workflow_ref,
                valid_from=now,
                valid_until=expires_at,
            )
        )
        if not registered:
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "GitHub workflow principal is revoked",
            )
        principal = Principal(
            id=authorization.principal_id,
            kind=authorization.kind,
            credential_version=self._policy.github_credential_version,
        )
        return self._issue(
            principal, authorization.scopes, now, GITHUB_TOKEN_TTL_SECONDS
        )

    async def exchange_worker(
        self, command: WorkerExchangeCommand
    ) -> BffExchangeResponse:
        """Authorize Windows HMAC/proof state and issue a ten-minute JWT."""
        now = self._dependencies.clock.now()
        authorization = await self._dependencies.worker.authorize(command.request, now)
        principal = Principal(
            id=authorization.principal_id,
            kind=PrincipalKind.WINDOWS_WORKER,
            credential_version=authorization.credential_version,
        )
        return self._issue(
            principal, authorization.scopes, now, WORKER_TOKEN_TTL_SECONDS
        )

    def _issue(
        self,
        principal: Principal,
        scopes: frozenset[Scope],
        now: datetime,
        lifetime_seconds: int,
    ) -> BffExchangeResponse:
        lifetime = timedelta(seconds=lifetime_seconds)
        token = self._dependencies.issuer.issue(
            IssueTokenRequest(
                principal=principal,
                audience=self._policy.audience,
                scopes=scopes,
                now=now,
                lifetime=lifetime,
            )
        )
        return BffExchangeResponse(
            access_token=token,
            expires_at=now + lifetime,
            scope=tuple(sorted(scopes, key=str)),
        )
