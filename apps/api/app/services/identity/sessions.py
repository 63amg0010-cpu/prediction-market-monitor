"""Opaque administrator session and same-origin CSRF primitives."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum, unique
from typing import TYPE_CHECKING, NewType, assert_never, final
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import SecretBytes, SecretStr

from app.core.errors import IdentityError, IdentityErrorCode

if TYPE_CHECKING:
    from app.core.principals import CredentialVersion

SessionId = NewType("SessionId", str)
SessionTokenDigest = NewType("SessionTokenDigest", bytes)
SESSION_LIFETIME = timedelta(hours=8)
SESSION_ROTATION_AGE = timedelta(hours=4)
CSRF_BUCKET_SECONDS = 900


@unique
class SessionState(StrEnum):
    """Durable administrator session state."""

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class AdminSessionRecord:
    """Persisted administrator session with only a token digest."""

    id: SessionId
    token_digest: SessionTokenDigest
    csrf_seed: SecretBytes
    credential_version: CredentialVersion
    issued_at: datetime
    rotate_at: datetime
    expires_at: datetime
    state: SessionState

    def with_state(self, state: SessionState) -> AdminSessionRecord:
        """Return an immutable state transition for repository persistence."""
        return replace(self, state=state)


@dataclass(frozen=True, slots=True)
class IssuedAdminSession:
    """One-time plaintext session token paired with its durable record."""

    token: SecretStr
    record: AdminSessionRecord


@dataclass(frozen=True, slots=True)
class SessionEvaluationRequest:
    """Inputs required to validate expiry, version, revocation, and rotation."""

    record: AdminSessionRecord
    presented_token: SecretStr
    now: datetime
    active_versions: frozenset[CredentialVersion]


@dataclass(frozen=True, slots=True)
class SessionEvaluation:
    """Authorized session state returned to a transactional repository adapter."""

    session_id: SessionId
    rotation_required: bool


@dataclass(frozen=True, slots=True)
class CsrfVerificationRequest:
    """Parsed CSRF token and request-origin inputs."""

    record: AdminSessionRecord
    token: str
    origin: str | None
    referer: str | None
    now: datetime


def hash_session_token(token: SecretStr) -> SessionTokenDigest:
    """Hash an opaque session token for constant-time repository comparison."""
    return SessionTokenDigest(
        hashlib.sha256(token.get_secret_value().encode()).digest()
    )


def issue_admin_session(
    now: datetime, credential_version: CredentialVersion
) -> IssuedAdminSession:
    """Issue an opaque eight-hour session that rotates after four hours."""
    token = SecretStr(secrets.token_urlsafe(32))
    record = AdminSessionRecord(
        id=SessionId(str(uuid4())),
        token_digest=hash_session_token(token),
        csrf_seed=SecretBytes(secrets.token_bytes(32)),
        credential_version=credential_version,
        issued_at=now,
        rotate_at=now + SESSION_ROTATION_AGE,
        expires_at=now + SESSION_LIFETIME,
        state=SessionState.ACTIVE,
    )
    return IssuedAdminSession(token=token, record=record)


def evaluate_session(request: SessionEvaluationRequest) -> SessionEvaluation:
    """Validate an opaque session and determine whether rotation is mandatory."""
    digest = hash_session_token(request.presented_token)
    if not hmac.compare_digest(request.record.token_digest, digest):
        raise IdentityError(
            IdentityErrorCode.INVALID_CREDENTIAL, "administrator session rejected"
        )
    match request.record.state:
        case SessionState.ACTIVE:
            pass
        case SessionState.REVOKED:
            raise IdentityError(
                IdentityErrorCode.SESSION_REVOKED,
                "administrator session was revoked",
            )
        case _:
            assert_never(request.record.state)
    if request.record.credential_version not in request.active_versions:
        raise IdentityError(
            IdentityErrorCode.SESSION_REVOKED,
            "administrator session credential was revoked",
        )
    if request.now >= request.record.expires_at:
        raise IdentityError(
            IdentityErrorCode.SESSION_EXPIRED, "administrator session expired"
        )
    return SessionEvaluation(
        session_id=request.record.id,
        rotation_required=request.now >= request.record.rotate_at,
    )


@final
class CsrfProtector:
    """Issue and verify origin-bound current/prior time-bucket CSRF tokens."""

    def __init__(
        self, *, signing_secret: SecretBytes, allowed_origins: frozenset[str]
    ) -> None:
        """Configure server signing material and exact same-origin allowlist."""
        self._signing_secret = signing_secret
        self._allowed_origins = allowed_origins

    def issue(self, record: AdminSessionRecord, now: datetime) -> str:
        """Issue a CSRF token for the current fifteen-minute bucket."""
        bucket = int(now.timestamp()) // CSRF_BUCKET_SECONDS
        signature = self._signature(record, bucket)
        encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        return f"{bucket}.{encoded}"

    def verify(self, request: CsrfVerificationRequest) -> None:
        """Require same-origin and a current or immediately prior bucket token."""
        if not self._origin_is_allowed(request.origin, request.referer):
            raise IdentityError(
                IdentityErrorCode.CSRF_REJECTED, "request origin rejected"
            )
        try:
            raw_bucket, encoded = request.token.split(".", maxsplit=1)
            bucket = int(raw_bucket)
            padding = "=" * (-len(encoded) % 4)
            supplied = base64.b64decode(
                encoded + padding, altchars=b"-_", validate=True
            )
        except (ValueError, binascii.Error) as error:
            raise IdentityError(
                IdentityErrorCode.CSRF_REJECTED, "CSRF token rejected"
            ) from error
        current = int(request.now.timestamp()) // CSRF_BUCKET_SECONDS
        if bucket not in {current, current - 1} or not hmac.compare_digest(
            self._signature(request.record, bucket), supplied
        ):
            raise IdentityError(IdentityErrorCode.CSRF_REJECTED, "CSRF token rejected")

    def _signature(self, record: AdminSessionRecord, bucket: int) -> bytes:
        payload = b"\n".join(
            (
                b"admin-csrf-v1",
                record.id.encode(),
                str(bucket).encode(),
                record.csrf_seed.get_secret_value(),
            )
        )
        return hmac.new(
            self._signing_secret.get_secret_value(), payload, hashlib.sha256
        ).digest()

    def _origin_is_allowed(self, origin: str | None, referer: str | None) -> bool:
        candidate = origin
        if candidate is None and referer is not None:
            parsed = urlsplit(referer)
            candidate = f"{parsed.scheme}://{parsed.netloc}"
        return candidate in self._allowed_origins
