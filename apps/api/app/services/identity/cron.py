"""Vercel cron static bearer validation."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING, final

from app.core.errors import IdentityError, IdentityErrorCode

if TYPE_CHECKING:
    from pydantic import SecretStr


@final
class CronCredentialVerifier:
    """Constant-time verifier for the exact Vercel Bearer contract."""

    def __init__(self, expected_secret: SecretStr) -> None:
        """Store only the SHA-256 verifier for constant-time comparison."""
        self._expected_digest = hashlib.sha256(
            expected_secret.get_secret_value().encode()
        ).digest()

    def verify(self, authorization: str | None) -> None:
        """Reject missing, malformed, or mismatched cron authorization."""
        supplied = ""
        if authorization is not None and authorization.startswith("Bearer "):
            supplied = authorization.removeprefix("Bearer ")
        supplied_digest = hashlib.sha256(supplied.encode()).digest()
        if not hmac.compare_digest(self._expected_digest, supplied_digest):
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL, "cron credential rejected"
            )
