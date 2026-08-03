# ruff: noqa: INP001

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx2
import pytest
from app.core.errors import IdentityError, IdentityErrorCode
from app.services.identity.github_oidc import GitHubJwksOidcVerifier
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import SecretStr

if TYPE_CHECKING:
    from app.domain.types import JsonValue

NOW = datetime(2026, 7, 22, 4, 0, tzinfo=UTC)


def _segment(value: JsonValue | bytes) -> str:
    encoded = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(encoded).rstrip(b"=").decode()


def _claims() -> dict[str, JsonValue]:
    timestamp = int(NOW.timestamp())
    return {
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
        "run_id": "1234",
        "run_attempt": "1",
        "jti": "nonce-1",
        "iat": timestamp,
        "nbf": timestamp,
        "exp": timestamp + 300,
    }


def _token(private_key: rsa.RSAPrivateKey) -> str:
    header = _segment({"alg": "RS256", "kid": "github-key", "typ": "JWT"})
    payload = _segment(_claims())
    signing_input = f"{header}.{payload}".encode()
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_segment(signature)}"


def _jwks(public_key: rsa.RSAPublicKey) -> dict[str, JsonValue]:
    numbers = public_key.public_numbers()
    modulus = numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")
    exponent = numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")
    return {
        "keys": [
            {
                "kty": "RSA",
                "kid": "github-key",
                "use": "sig",
                "alg": "RS256",
                "n": _segment(modulus),
                "e": _segment(exponent),
            }
        ]
    }


@pytest.mark.asyncio
async def test_github_oidc_verifier_accepts_only_a_matching_rs256_signature() -> None:
    # Given: a GitHub-shaped token and the matching issuer JWKS.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(200, json=_jwks(key.public_key()))
    )
    verifier = GitHubJwksOidcVerifier(transport)

    # When: the production verifier fetches the JWKS and verifies the token.
    claims = await verifier.verify(SecretStr(_token(key)), NOW)

    # Then: typed claims are returned only after cryptographic verification.
    assert claims.repository == "owner/monitor"
    assert claims.run_id == "1234"


@pytest.mark.asyncio
async def test_github_oidc_verifier_rejects_a_signature_from_another_key() -> None:
    # Given: a token signed by a key not present in the issuer JWKS.
    trusted = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(200, json=_jwks(trusted.public_key()))
    )
    verifier = GitHubJwksOidcVerifier(transport)

    # When/Then: signature mismatch is a redacted OIDC claim rejection.
    with pytest.raises(IdentityError) as captured:
        _ = await verifier.verify(SecretStr(_token(attacker)), NOW)
    assert captured.value.code is IdentityErrorCode.INVALID_OIDC_CLAIMS


@pytest.mark.parametrize(
    "token",
    [
        "not-a-compact-jwt",
        "only.two",
        "too.many.jwt.segments",
        "***.***.***",
    ],
)
@pytest.mark.asyncio
async def test_github_oidc_verifier_rejects_malformed_compact_jwt(
    token: str,
) -> None:
    # Given: malformed compact input and a transport that must never be reached.
    verifier = GitHubJwksOidcVerifier(
        httpx2.MockTransport(lambda _: pytest.fail("JWKS network was reached"))
    )

    # When/Then: parsing fails closed before any key lookup.
    with pytest.raises(IdentityError) as captured:
        _ = await verifier.verify(SecretStr(token), NOW)
    assert captured.value.code is IdentityErrorCode.INVALID_OIDC_CLAIMS
