"""Production identity adapter construction from validated settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import SecretBytes

from app.core.jwt import Ed25519TokenIssuer, Ed25519TokenVerifier
from app.core.principals import CredentialVersion, Scope
from app.services.dashboard.sql_authorizer import SqlScopeAuthorizer
from app.services.identity.bff import (
    BffCredential,
    BffExchangeAuthorizer,
    CredentialState,
)
from app.services.identity.exchanges import (
    IdentityExchangeDependencies,
    IdentityExchangePolicy,
    IdentityExchangeService,
    SystemClock,
)
from app.services.identity.github import (
    GitHubClaimPolicy,
    GitHubExchangeAuthorizer,
    GitHubWorkflowRule,
)
from app.services.identity.github_oidc import GitHubJwksOidcVerifier
from app.services.identity.sql_principals import (
    SqlPrincipalAuthorizationRepository,
    SqlWorkerApprovalRepository,
)
from app.services.identity.sql_replay import SqlNonceRepository, SqlRateLimitRepository
from app.services.identity.windows import (
    WorkerBootstrapVerifier,
    WorkerExchangeAuthorizer,
)

if TYPE_CHECKING:
    from app.core.settings import IdentitySettings
    from app.db.session import DatabaseSessions
    from app.services.dashboard.ports import ScopeAuthorizer
    from app.services.identity.exchanges import ServiceTokenExchangeHandler

_ISSUER: Final = "monitor-api"
_GITHUB_CREDENTIAL_VERSION: Final = CredentialVersion("1")
_KEY_TYPE_ERROR: Final = "service_token_key_type_invalid"
_WORKFLOW_ERROR: Final = "github_workflow_not_supported"
_WORKFLOW_MISSING_ERROR: Final = "github_workflow_missing"
_COLLECTOR_SCOPES: Final = frozenset(
    {
        Scope.COLLECTOR_MATERIALIZE,
        Scope.COLLECTOR_RESERVE,
        Scope.COLLECTOR_CLAIM,
        Scope.COLLECTOR_PAGE_COMMIT,
        Scope.COLLECTOR_HEARTBEAT,
        Scope.COLLECTOR_COMPLETE,
    }
)
_VERIFIER_SCOPES: Final = frozenset({Scope.VERIFY_READ, Scope.VERIFY_WRITE})


@dataclass(frozen=True, slots=True)
class IdentityAdapters:
    """Fully composed token exchange and durable scope authorization pair."""

    exchange: ServiceTokenExchangeHandler
    scopes: ScopeAuthorizer


def build_identity_adapters(
    settings: IdentitySettings, sessions: DatabaseSessions
) -> IdentityAdapters:
    """Build all identity paths together or raise without partial adapters."""
    private_key = serialization.load_pem_private_key(
        settings.service_token_issuer_private_key.get_secret_value().encode(),
        password=None,
    )
    public_key = serialization.load_pem_public_key(
        settings.service_token_issuer_public_key.get_secret_value().encode()
    )
    if not isinstance(private_key, Ed25519PrivateKey) or not isinstance(
        public_key, Ed25519PublicKey
    ):
        raise TypeError(_KEY_TYPE_ERROR)
    metadata = settings.redacted_metadata()
    nonces = SqlNonceRepository(sessions)
    principals = SqlPrincipalAuthorizationRepository(sessions)
    issuer = Ed25519TokenIssuer(
        issuer=_ISSUER,
        active_key_id=settings.service_token_key_id,
        private_key=private_key,
    )
    verifier = Ed25519TokenVerifier(
        issuer=_ISSUER,
        public_keys={settings.service_token_key_id: public_key},
    )
    exchange = IdentityExchangeService(
        IdentityExchangeDependencies(
            clock=SystemClock(),
            issuer=issuer,
            bff=BffExchangeAuthorizer(
                credentials=(
                    BffCredential.from_secret(
                        CredentialVersion(settings.bff_credential_version),
                        settings.bff_client_credential,
                        state=CredentialState.ACTIVE,
                    ),
                ),
                nonces=nonces,
                rate_limits=SqlRateLimitRepository(sessions),
            ),
            github_verifier=GitHubJwksOidcVerifier(),
            github=GitHubExchangeAuthorizer(_github_policy(settings), nonces),
            github_principals=principals,
            worker=WorkerExchangeAuthorizer(
                verifier=WorkerBootstrapVerifier(
                    {
                        CredentialVersion(
                            settings.worker_credential_version
                        ): SecretBytes(
                            settings.worker_bootstrap_secret.get_secret_value().encode()
                        ),
                    }
                ),
                approvals=SqlWorkerApprovalRepository(sessions),
                nonces=nonces,
            ),
        ),
        IdentityExchangePolicy(metadata["audience"], _GITHUB_CREDENTIAL_VERSION),
    )
    return IdentityAdapters(
        exchange,
        SqlScopeAuthorizer(verifier, principals, metadata["audience"]),
    )


def _github_policy(settings: IdentitySettings) -> GitHubClaimPolicy:
    rules: list[GitHubWorkflowRule] = []
    triples = zip(
        settings.github_workflow_refs,
        settings.github_allowed_refs,
        settings.github_allowed_environments,
        strict=True,
    )
    for workflow_ref, git_ref, environment in triples:
        if "/collect.yml@" in workflow_ref:
            scopes = _COLLECTOR_SCOPES
        elif "/verify.yml@" in workflow_ref:
            scopes = _VERIFIER_SCOPES
        else:
            raise ValueError(_WORKFLOW_ERROR)
        rules.append(GitHubWorkflowRule(workflow_ref, git_ref, environment, scopes))
    if not rules:
        raise ValueError(_WORKFLOW_MISSING_ERROR)
    return GitHubClaimPolicy(settings.github_repository, tuple(rules))


__all__ = ("IdentityAdapters", "build_identity_adapters")
