"""Ed25519 service-token issuance and verification."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal, final, override
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .principals import (  # noqa: TC001 - Pydantic resolves these at runtime.
    CredentialVersion,
    Principal,
    PrincipalId,
    PrincipalKind,
    Scope,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

TOKEN_SKEW_SECONDS = 30
JWT_SEGMENT_COUNT = 3
NonBlank = Annotated[str, StringConstraints(min_length=1)]


@final
@dataclass(frozen=True, slots=True)
class TokenRejectedError(Exception):
    """A deliberately non-specific service-token rejection."""

    reason: str = "service token rejected"

    @override
    def __str__(self) -> str:
        """Return the deliberately non-specific rejection reason."""
        return self.reason


class _TokenHeader(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)

    algorithm: Literal["EdDSA"] = Field(alias="alg")
    key_id: NonBlank = Field(alias="kid")
    token_type: Literal["JWT"] = Field(alias="typ")


class AccessTokenClaims(BaseModel):
    """Validated service-token claims with typed scopes and principal kind."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, populate_by_name=True)

    issuer: NonBlank = Field(alias="iss")
    subject: PrincipalId = Field(alias="sub")
    audience: NonBlank = Field(alias="aud")
    scopes: tuple[Scope, ...] = Field(alias="scope", min_length=1)
    jwt_id: NonBlank = Field(alias="jti")
    issued_at: int = Field(alias="iat")
    not_before: int = Field(alias="nbf")
    expires_at: int = Field(alias="exp")
    credential_version: CredentialVersion
    principal_kind: PrincipalKind


@dataclass(frozen=True, slots=True)
class IssueTokenRequest:
    """Complete immutable input to token issuance."""

    principal: Principal
    audience: str
    scopes: frozenset[Scope]
    now: datetime
    lifetime: timedelta


@dataclass(frozen=True, slots=True)
class VerifyTokenRequest:
    """Complete immutable input to token verification."""

    token: str
    audience: str
    required_scopes: frozenset[Scope]
    now: datetime


@dataclass(frozen=True, slots=True)
class PrincipalCredentialState:
    """Atomic repository result used to enforce rotation and revocation."""

    principal_id: PrincipalId
    active_versions: frozenset[CredentialVersion]
    revoked: bool


@final
class Ed25519TokenIssuer:
    """Issue non-refreshable compact JWTs with the active Ed25519 key."""

    def __init__(
        self, *, issuer: str, active_key_id: str, private_key: Ed25519PrivateKey
    ) -> None:
        """Configure the sole active signing key and issuer."""
        self._issuer = issuer
        self._active_key_id = active_key_id
        self._private_key = private_key

    def issue(self, request: IssueTokenRequest) -> str:
        """Issue a scoped JWT with all mandatory identity claims."""
        if request.lifetime <= timedelta(0) or not request.scopes:
            raise TokenRejectedError
        now = _timestamp(request.now)
        claims = AccessTokenClaims(
            iss=self._issuer,
            sub=request.principal.id,
            aud=request.audience,
            scope=tuple(sorted(request.scopes, key=str)),
            jti=str(uuid4()),
            iat=now,
            nbf=now,
            exp=now + int(request.lifetime.total_seconds()),
            credential_version=request.principal.credential_version,
            principal_kind=request.principal.kind,
        )
        header = _TokenHeader(alg="EdDSA", kid=self._active_key_id, typ="JWT")
        signing_input = b".".join(
            (
                _encode_segment(header.model_dump_json(by_alias=True).encode()),
                _encode_segment(claims.model_dump_json(by_alias=True).encode()),
            )
        )
        signature = self._private_key.sign(signing_input)
        return b".".join((signing_input, _encode_segment(signature))).decode("ascii")


@final
class Ed25519TokenVerifier:
    """Verify compact Ed25519 JWTs against an explicit key allowlist."""

    def __init__(
        self, *, issuer: str, public_keys: Mapping[str, Ed25519PublicKey]
    ) -> None:
        """Configure an explicit verification-key rotation allowlist."""
        self._issuer = issuer
        self._public_keys = dict(public_keys)

    def verify(self, request: VerifyTokenRequest) -> AccessTokenClaims:
        """Verify signature, key, time, audience, and required scopes."""
        segments = request.token.split(".")
        if len(segments) != JWT_SEGMENT_COUNT:
            raise TokenRejectedError
        try:
            header = _TokenHeader.model_validate_json(_decode_segment(segments[0]))
            claims = AccessTokenClaims.model_validate_json(_decode_segment(segments[1]))
            signature = _decode_segment(segments[2])
        except (binascii.Error, ValidationError, UnicodeEncodeError) as error:
            raise TokenRejectedError from error
        public_key = self._public_keys.get(header.key_id)
        if public_key is None:
            raise TokenRejectedError
        signing_input = f"{segments[0]}.{segments[1]}".encode("ascii")
        try:
            public_key.verify(signature, signing_input)
        except InvalidSignature as error:
            raise TokenRejectedError from error
        now = _timestamp(request.now)
        invalid_time = (
            claims.issued_at > now + TOKEN_SKEW_SECONDS
            or claims.not_before > now + TOKEN_SKEW_SECONDS
            or claims.expires_at < now - TOKEN_SKEW_SECONDS
            or claims.expires_at <= claims.not_before
        )
        if (
            invalid_time
            or claims.issuer != self._issuer
            or claims.audience != request.audience
            or not request.required_scopes.issubset(claims.scopes)
        ):
            raise TokenRejectedError
        return claims


def enforce_credential_state(
    claims: AccessTokenClaims, state: PrincipalCredentialState
) -> None:
    """Reject revoked principals and credentials outside the active allowlist."""
    if (
        state.revoked
        or state.principal_id != claims.subject
        or claims.credential_version not in state.active_versions
    ):
        raise TokenRejectedError


def _timestamp(value: datetime) -> int:
    if value.utcoffset() is None:
        raise TokenRejectedError
    return int(value.timestamp())


def _encode_segment(value: bytes) -> bytes:
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _decode_segment(value: str) -> bytes:
    encoded = value.encode("ascii")
    padding = b"=" * (-len(encoded) % 4)
    return base64.b64decode(encoded + padding, altchars=b"-_", validate=True)
