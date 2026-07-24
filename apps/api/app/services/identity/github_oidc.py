"""GitHub Actions RS256 OIDC verification against the issuer JWKS."""

from __future__ import annotations

import base64
import binascii
import socket
from typing import TYPE_CHECKING, ClassVar, Final, Literal, final

import httpx2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from app.core.errors import IdentityError, IdentityErrorCode

from .github import GITHUB_OIDC_ISSUER, GitHubOIDCClaims

GITHUB_JWKS_URL = f"{GITHUB_OIDC_ISSUER}/.well-known/jwks"
JWT_SEGMENT_COUNT: Final = 3

if TYPE_CHECKING:
    from datetime import datetime


class _OidcHeader(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    algorithm: Literal["RS256"] = Field(alias="alg")
    key_id: str = Field(alias="kid", min_length=1)
    token_type: Literal["JWT"] = Field(alias="typ")


class _Jwk(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    key_type: Literal["RSA"] = Field(alias="kty")
    key_id: str = Field(alias="kid", min_length=1)
    use: Literal["sig"]
    algorithm: Literal["RS256"] = Field(alias="alg")
    modulus: str = Field(alias="n", min_length=1)
    exponent: str = Field(alias="e", min_length=1)


class _Jwks(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")

    keys: tuple[_Jwk, ...] = Field(min_length=1)


@final
class GitHubJwksOidcVerifier:
    """Fetch trusted keys, verify RS256, then parse typed GitHub claims."""

    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        """Allow an explicit HTTP transport while production uses direct TLS."""
        self._transport: httpx2.AsyncBaseTransport | None = transport

    async def verify(self, token: SecretStr, now: datetime) -> GitHubOIDCClaims:
        """Verify one compact token against the current issuer key set."""
        del now
        segments = token.get_secret_value().split(".")
        if len(segments) != JWT_SEGMENT_COUNT:
            raise _invalid_claims()
        try:
            header = _OidcHeader.model_validate_json(_decode(segments[0]))
            transport = self._transport or httpx2.AsyncHTTPTransport(
                http2=True,
                retries=3,
                limits=httpx2.Limits(
                    max_connections=200,
                    max_keepalive_connections=40,
                    keepalive_expiry=30.0,
                ),
                socket_options=[(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)],
            )
            async with httpx2.AsyncClient(
                timeout=httpx2.Timeout(
                    connect=5.0,
                    read=30.0,
                    write=10.0,
                    pool=10.0,
                ),
                follow_redirects=False,
                trust_env=False,
                transport=transport,
            ) as client:
                response = await client.get(GITHUB_JWKS_URL)
                _ = response.raise_for_status()
            jwks = _Jwks.model_validate_json(response.content)
            key = next(
                (
                    candidate
                    for candidate in jwks.keys
                    if candidate.key_id == header.key_id
                ),
                None,
            )
            if key is None:
                raise _invalid_claims()
            public_key = rsa.RSAPublicNumbers(
                _unsigned_int(key.exponent), _unsigned_int(key.modulus)
            ).public_key()
            public_key.verify(
                _decode(segments[2]),
                f"{segments[0]}.{segments[1]}".encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            return GitHubOIDCClaims.model_validate_json(_decode(segments[1]))
        except httpx2.HTTPError as error:
            raise IdentityError(
                IdentityErrorCode.SERVICE_UNAVAILABLE,
                "GitHub OIDC verification unavailable",
            ) from error
        except IdentityError:
            raise
        except (
            InvalidSignature,
            ValidationError,
            ValueError,
            binascii.Error,
            UnicodeEncodeError,
        ) as error:
            raise _invalid_claims() from error


def _decode(value: str) -> bytes:
    encoded = value.encode("ascii")
    return base64.b64decode(
        encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
    )


def _unsigned_int(value: str) -> int:
    return int.from_bytes(_decode(value), "big")


def _invalid_claims() -> IdentityError:
    return IdentityError(
        IdentityErrorCode.INVALID_OIDC_CLAIMS,
        "GitHub OIDC token rejected",
    )


__all__ = ("GITHUB_JWKS_URL", "GitHubJwksOidcVerifier")
