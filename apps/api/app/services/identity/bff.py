"""Server-only BFF credential exchange authorization."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum, unique
from typing import TYPE_CHECKING, assert_never, final

from app.core.errors import IdentityError, IdentityErrorCode
from app.core.principals import CredentialVersion, PrincipalId, Scope

from .ports import NonceRepository, RateLimitRepository, RateLimitRule

if TYPE_CHECKING:
    from pydantic import SecretStr

BFF_TOKEN_TTL_SECONDS = 300
BFF_CACHE_MAX_SECONDS = 240
BFF_NONCE_RETENTION_SECONDS = 600
BFF_EXCHANGE_RULE = RateLimitRule("bff-exchange", 60, 60)
BFF_FAILURE_RULE = RateLimitRule("bff-nonce-failure", 10, 60)
_BFF_SCOPE_ALLOWLIST = frozenset(
    {
        frozenset({Scope.BFF_AUTH, Scope.BFF_READ}),
        frozenset({Scope.ADMIN_COMMAND}),
    }
)


@unique
class CredentialState(StrEnum):
    """Two-phase credential rotation state."""

    ACTIVE = "active"
    GRACE = "grace"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class BffCredential:
    """A versioned SHA-256 credential verifier, never a raw credential."""

    version: CredentialVersion
    verifier: bytes
    state: CredentialState

    @classmethod
    def from_secret(
        cls,
        version: CredentialVersion,
        secret: SecretStr,
        *,
        state: CredentialState,
    ) -> BffCredential:
        """Build the stored verifier at the trusted configuration boundary."""
        return cls(
            version=version,
            verifier=hashlib.sha256(secret.get_secret_value().encode()).digest(),
            state=state,
        )


@dataclass(frozen=True, slots=True)
class BffExchangeRequest:
    """Parsed server-to-server BFF exchange request."""

    credential_version: CredentialVersion
    presented_credential: SecretStr
    request_nonce: str
    requested_scopes: frozenset[Scope]
    deployment_identity: str

    def with_scopes(self, scopes: frozenset[Scope]) -> BffExchangeRequest:
        """Return the same request with a different parsed scope set."""
        return replace(self, requested_scopes=scopes)


@dataclass(frozen=True, slots=True)
class BffAuthorization:
    """Authorized principal material passed to token issuance."""

    principal_id: PrincipalId
    credential_version: CredentialVersion
    scopes: frozenset[Scope]


@final
class BffExchangeAuthorizer:
    """Enforce BFF credential, scope, rate, nonce, and rotation policy."""

    def __init__(
        self,
        *,
        credentials: tuple[BffCredential, ...],
        nonces: NonceRepository,
        rate_limits: RateLimitRepository,
    ) -> None:
        """Configure rotation credentials and atomic replay/rate repositories."""
        self._credentials = credentials
        self._nonces = nonces
        self._rate_limits = rate_limits

    async def authorize(
        self, request: BffExchangeRequest, now: datetime
    ) -> BffAuthorization:
        """Authorize one server-only exchange and consume its nonce."""
        rate = await self._rate_limits.consume(
            request.deployment_identity, BFF_EXCHANGE_RULE, now
        )
        if not rate.allowed:
            raise IdentityError(
                IdentityErrorCode.RATE_LIMITED,
                "BFF exchange rate limit exceeded",
                rate.retry_after_seconds,
            )
        if request.requested_scopes not in _BFF_SCOPE_ALLOWLIST:
            raise IdentityError(
                IdentityErrorCode.INVALID_SCOPE, "requested scopes are not allowed"
            )
        credential = self._match_credential(request)
        if credential is None or not _state_allows_exchange(credential.state):
            failure = await self._rate_limits.consume(
                request.deployment_identity, BFF_FAILURE_RULE, now
            )
            if not failure.allowed:
                raise IdentityError(
                    IdentityErrorCode.RATE_LIMITED,
                    "BFF credential failure rate limit exceeded",
                    failure.retry_after_seconds,
                )
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL, "BFF credential rejected"
            )
        retained = now + timedelta(seconds=BFF_NONCE_RETENTION_SECONDS)
        consumed = await self._nonces.consume_once(
            "bff", request.request_nonce, retained
        )
        if not consumed:
            raise IdentityError(
                IdentityErrorCode.REPLAYED_NONCE, "request nonce was already used"
            )
        return BffAuthorization(
            principal_id=PrincipalId(f"bff:{request.deployment_identity}"),
            credential_version=credential.version,
            scopes=request.requested_scopes,
        )

    def _match_credential(self, request: BffExchangeRequest) -> BffCredential | None:
        presented = hashlib.sha256(
            request.presented_credential.get_secret_value().encode()
        ).digest()
        matched: BffCredential | None = None
        for credential in self._credentials:
            version_matches = hmac.compare_digest(
                credential.version.encode(), request.credential_version.encode()
            )
            secret_matches = hmac.compare_digest(credential.verifier, presented)
            if version_matches and secret_matches:
                matched = credential
        return matched


def _state_allows_exchange(state: CredentialState) -> bool:
    match state:
        case CredentialState.ACTIVE | CredentialState.GRACE:
            return True
        case CredentialState.REVOKED:
            return False
        case _:
            assert_never(state)
