# ruff: noqa: INP001
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.errors import IdentityError, IdentityErrorCode
from app.core.principals import CredentialVersion, Scope
from app.services.identity.bff import (
    BFF_CACHE_MAX_SECONDS,
    BFF_TOKEN_TTL_SECONDS,
    BffCredential,
    BffExchangeAuthorizer,
    BffExchangeRequest,
    CredentialState,
)
from app.services.identity.ports import RateLimitDecision, RateLimitRule
from pydantic import SecretStr


class _NonceStore:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    async def consume_once(
        self, namespace: str, key: str, retain_until: datetime
    ) -> bool:
        del retain_until
        namespaced = f"{namespace}:{key}"
        if namespaced in self.seen:
            return False
        self.seen.add(namespaced)
        return True


class _RateLimiter:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed: bool = allowed
        self.rules: list[RateLimitRule] = []

    async def consume(
        self, key: str, rule: RateLimitRule, now: datetime
    ) -> RateLimitDecision:
        del key, now
        self.rules.append(rule)
        return RateLimitDecision(allowed=self.allowed, retry_after_seconds=60)


def _credential(version: str, secret: str, state: CredentialState) -> BffCredential:
    return BffCredential.from_secret(
        CredentialVersion(version), SecretStr(secret), state=state
    )


def _request(version: str, secret: str, nonce: str = "nonce-1") -> BffExchangeRequest:
    return BffExchangeRequest(
        credential_version=CredentialVersion(version),
        presented_credential=SecretStr(secret),
        request_nonce=nonce,
        requested_scopes=frozenset({Scope.BFF_AUTH, Scope.BFF_READ}),
        deployment_identity="vercel-production",
    )


@pytest.mark.asyncio
async def test_bff_nonce_is_one_use() -> None:
    # Given
    authorizer = BffExchangeAuthorizer(
        credentials=(_credential("v1", "a" * 32, CredentialState.ACTIVE),),
        nonces=_NonceStore(),
        rate_limits=_RateLimiter(),
    )
    now = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    request = _request("v1", "a" * 32)
    _ = await authorizer.authorize(request, now)

    # When / Then
    with pytest.raises(IdentityError) as raised:
        _ = await authorizer.authorize(request, now)
    assert raised.value.code is IdentityErrorCode.REPLAYED_NONCE


@pytest.mark.asyncio
async def test_bff_rejects_invalid_scope_set() -> None:
    # Given
    authorizer = BffExchangeAuthorizer(
        credentials=(_credential("v1", "a" * 32, CredentialState.ACTIVE),),
        nonces=_NonceStore(),
        rate_limits=_RateLimiter(),
    )
    request = _request("v1", "a" * 32)
    request = request.with_scopes(frozenset({Scope.BFF_READ}))

    # When / Then
    with pytest.raises(IdentityError) as raised:
        _ = await authorizer.authorize(request, datetime(2026, 7, 21, tzinfo=UTC))
    assert raised.value.code is IdentityErrorCode.INVALID_SCOPE


@pytest.mark.asyncio
async def test_bff_rate_limit_is_fail_closed() -> None:
    # Given
    limiter = _RateLimiter(allowed=False)
    authorizer = BffExchangeAuthorizer(
        credentials=(_credential("v1", "a" * 32, CredentialState.ACTIVE),),
        nonces=_NonceStore(),
        rate_limits=limiter,
    )

    # When / Then
    with pytest.raises(IdentityError) as raised:
        _ = await authorizer.authorize(
            _request("v1", "a" * 32), datetime(2026, 7, 21, tzinfo=UTC)
        )
    assert raised.value.code is IdentityErrorCode.RATE_LIMITED
    assert limiter.rules[0].limit == 60
    assert limiter.rules[0].window_seconds == 60


@pytest.mark.asyncio
async def test_bff_normal_and_forced_rotation() -> None:
    # Given
    old = _credential("v1", "a" * 32, CredentialState.GRACE)
    new = _credential("v2", "b" * 32, CredentialState.ACTIVE)
    now = datetime(2026, 7, 21, tzinfo=UTC)
    rotating = BffExchangeAuthorizer(
        credentials=(old, new), nonces=_NonceStore(), rate_limits=_RateLimiter()
    )

    # When
    old_auth = await rotating.authorize(_request("v1", "a" * 32, "old"), now)
    new_auth = await rotating.authorize(_request("v2", "b" * 32, "new"), now)

    # Then
    assert old_auth.credential_version == CredentialVersion("v1")
    assert new_auth.credential_version == CredentialVersion("v2")
    assert BFF_TOKEN_TTL_SECONDS == 300
    assert BFF_CACHE_MAX_SECONDS == 240

    # Given forced revocation
    forced = BffExchangeAuthorizer(
        credentials=(
            _credential("v1", "a" * 32, CredentialState.REVOKED),
            new,
        ),
        nonces=_NonceStore(),
        rate_limits=_RateLimiter(),
    )

    # When / Then
    with pytest.raises(IdentityError) as raised:
        _ = await forced.authorize(_request("v1", "a" * 32), now)
    assert raised.value.code is IdentityErrorCode.INVALID_CREDENTIAL
