"""Argon2id administrator password and login rate-limit primitives."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, final

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
from pydantic import SecretStr

from app.core.errors import IdentityError, IdentityErrorCode

from .sessions import AdminSessionRecord, IssuedAdminSession, issue_admin_session

if TYPE_CHECKING:
    from datetime import datetime

    from app.core.principals import CredentialVersion

LOGIN_FAILURE_LIMIT = 5
LOGIN_FAILURE_WINDOW_SECONDS = 900


@final
class AdminPasswordVerifier:
    """Verify the configured administrator password using Argon2id."""

    def __init__(self, encoded_hash: SecretStr) -> None:
        """Configure the required Argon2id hash."""
        self._encoded_hash = encoded_hash
        self._hasher = PasswordHasher(type=Type.ID)

    def verify(self, password: SecretStr) -> bool:
        """Return whether the password matches without exposing either value."""
        try:
            return self._hasher.verify(
                self._encoded_hash.get_secret_value(), password.get_secret_value()
            )
        except VerifyMismatchError:
            return False

    def needs_rehash(self) -> bool:
        """Return whether current Argon2id policy requires hash rotation."""
        return self._hasher.check_needs_rehash(self._encoded_hash.get_secret_value())

    @staticmethod
    def hash_password(password: SecretStr) -> SecretStr:
        """Create an Argon2id administrator password hash."""
        return SecretStr(PasswordHasher(type=Type.ID).hash(password.get_secret_value()))


@dataclass(frozen=True, slots=True)
class AdminLoginRequest:
    """Parsed administrator login attempt."""

    password: SecretStr
    client_ip: str
    now: datetime
    credential_version: CredentialVersion


class LoginFailureRepository(Protocol):
    """Atomically enforce five failed logins per IP over fifteen minutes."""

    async def is_allowed(self, client_ip: str, now: datetime) -> bool:
        """Return whether the IP may attempt authentication."""
        ...

    async def record_failure(self, client_ip: str, now: datetime) -> bool:
        """Record one failure and return whether another attempt remains."""
        ...

    async def clear(self, client_ip: str) -> None:
        """Clear failures after a successful login."""
        ...


class AdminSessionRepository(Protocol):
    """Persist opaque administrator session records, never raw tokens."""

    async def create(self, record: AdminSessionRecord) -> None:
        """Persist a session record without its plaintext token."""
        ...


@final
class AdminLoginService:
    """Apply login rate limiting before creating an eight-hour session."""

    def __init__(
        self,
        *,
        password_verifier: AdminPasswordVerifier,
        failures: LoginFailureRepository,
        sessions: AdminSessionRepository,
    ) -> None:
        """Configure password, failure, and durable session collaborators."""
        self._password_verifier = password_verifier
        self._failures = failures
        self._sessions = sessions

    async def login(self, request: AdminLoginRequest) -> IssuedAdminSession:
        """Create a session only after password and rate-limit authorization."""
        if not await self._failures.is_allowed(request.client_ip, request.now):
            raise IdentityError(
                IdentityErrorCode.RATE_LIMITED,
                "administrator login rate limit exceeded",
                LOGIN_FAILURE_WINDOW_SECONDS,
            )
        if not self._password_verifier.verify(request.password):
            still_allowed = await self._failures.record_failure(
                request.client_ip, request.now
            )
            if not still_allowed:
                raise IdentityError(
                    IdentityErrorCode.RATE_LIMITED,
                    "administrator login rate limit exceeded",
                    LOGIN_FAILURE_WINDOW_SECONDS,
                )
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "administrator credentials rejected",
            )
        await self._failures.clear(request.client_ip)
        issued = issue_admin_session(request.now, request.credential_version)
        await self._sessions.create(issued.record)
        return issued
