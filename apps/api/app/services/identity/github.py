"""GitHub OIDC claim policy independent of network/JWKS retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, final

from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import IdentityError, IdentityErrorCode
from app.core.jwt import TOKEN_SKEW_SECONDS
from app.core.principals import PrincipalId, PrincipalKind, Scope

if TYPE_CHECKING:
    from .ports import NonceRepository

GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
GITHUB_OIDC_AUDIENCE = "monitor-control"


class GitHubOIDCClaims(BaseModel):
    """Signature-verified GitHub OIDC claims parsed at the adapter boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)

    issuer: str = Field(alias="iss")
    audience: str = Field(alias="aud")
    subject: str = Field(alias="sub")
    repository: str
    repository_id: str = Field(pattern=r"^[1-9][0-9]*$")
    repository_owner_id: str = Field(pattern=r"^[1-9][0-9]*$")
    workflow_ref: str
    job_workflow_ref: str | None = None
    git_ref: str = Field(alias="ref")
    head_sha: str = Field(alias="sha", pattern=r"^[0-9a-f]{40}$")
    environment: str | None = None
    run_id: str
    run_attempt: str
    jwt_id: str = Field(alias="jti")
    issued_at: int = Field(alias="iat")
    not_before: int = Field(alias="nbf")
    expires_at: int = Field(alias="exp")


@dataclass(frozen=True, slots=True)
class GitHubWorkflowRule:
    """Exact workflow identity and its sole permitted scope set."""

    workflow_ref: str
    git_ref: str
    environment: str
    scopes: frozenset[Scope]


@dataclass(frozen=True, slots=True)
class GitHubAuthorization:
    """Policy-approved GitHub run identity."""

    principal_id: PrincipalId
    kind: PrincipalKind
    scopes: frozenset[Scope]
    replay_key: str
    retain_until: datetime


@dataclass(frozen=True, slots=True)
class GitHubClaimPolicy:
    """Fail-closed allowlist for one repository and exact workflow rules."""

    repository: str
    workflows: tuple[GitHubWorkflowRule, ...]

    def validate_verified_claims(
        self, claims: GitHubOIDCClaims, now: datetime
    ) -> GitHubAuthorization:
        """Validate already signature-verified claims without live network access."""
        now_seconds = int(now.timestamp())
        time_valid = (
            claims.issued_at <= now_seconds + TOKEN_SKEW_SECONDS
            and claims.not_before <= now_seconds + TOKEN_SKEW_SECONDS
            and claims.expires_at >= now_seconds - TOKEN_SKEW_SECONDS
            and claims.expires_at > claims.not_before
        )
        rule = next(
            (
                candidate
                for candidate in self.workflows
                if candidate.workflow_ref == claims.workflow_ref
                and candidate.git_ref == claims.git_ref
                and candidate.environment == claims.environment
            ),
            None,
        )
        if (
            claims.issuer != GITHUB_OIDC_ISSUER
            or claims.audience != GITHUB_OIDC_AUDIENCE
            or claims.repository != self.repository
            or not claims.run_id.isdecimal()
            or not claims.run_attempt.isdecimal()
            or not time_valid
            or rule is None
        ):
            raise IdentityError(
                IdentityErrorCode.INVALID_OIDC_CLAIMS,
                "GitHub OIDC claims are outside the deployment policy",
            )
        verifier = Scope.VERIFY_READ in rule.scopes or Scope.VERIFY_WRITE in rule.scopes
        kind = (
            PrincipalKind.GITHUB_VERIFIER
            if verifier
            else PrincipalKind.GITHUB_COLLECTOR
        )
        return GitHubAuthorization(
            principal_id=PrincipalId(f"github:{claims.repository}:{claims.run_id}"),
            kind=kind,
            scopes=rule.scopes,
            replay_key=f"{claims.run_id}|{claims.workflow_ref}|{claims.run_attempt}|{claims.jwt_id}",
            retain_until=datetime.fromtimestamp(claims.expires_at, tz=UTC),
        )


def expected_subject(claims: GitHubOIDCClaims, context: str) -> str:
    """Build GitHub's immutable repository subject from signed claim IDs."""
    owner, repository = claims.repository.split("/", maxsplit=1)
    return (
        f"repo:{owner}@{claims.repository_owner_id}/"
        f"{repository}@{claims.repository_id}:{context}"
    )


@final
class GitHubExchangeAuthorizer:
    """Apply claim policy and atomically consume the workflow-run nonce."""

    def __init__(self, policy: GitHubClaimPolicy, nonces: NonceRepository) -> None:
        """Configure exact claim policy and durable nonce storage."""
        self._policy = policy
        self._nonces = nonces

    async def authorize(
        self, claims: GitHubOIDCClaims, now: datetime
    ) -> GitHubAuthorization:
        """Authorize one verified OIDC exchange exactly once."""
        authorization = self._policy.validate_verified_claims(claims, now)
        consumed = await self._nonces.consume_once(
            "github-oidc", authorization.replay_key, authorization.retain_until
        )
        if not consumed:
            raise IdentityError(
                IdentityErrorCode.REPLAYED_NONCE,
                "GitHub workflow identity was already exchanged",
            )
        return authorization
