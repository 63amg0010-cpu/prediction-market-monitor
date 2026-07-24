"""Ed25519 bearer verification backed by durable principal state."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, final

from app.core.errors import IdentityError, IdentityErrorCode
from app.core.jwt import TokenRejectedError, VerifyTokenRequest
from app.services.identity.ports import PrincipalAuthorizationRequest

from .models import AuthorizedService

if TYPE_CHECKING:
    from pydantic import SecretStr

    from app.core.jwt import Ed25519TokenVerifier
    from app.core.principals import Scope
    from app.services.identity.ports import PrincipalAuthorizationRepository


@final
class SqlScopeAuthorizer:
    """Verify token cryptography and atomically enforce durable revocation."""

    def __init__(
        self,
        verifier: Ed25519TokenVerifier,
        principals: PrincipalAuthorizationRepository,
        audience: str,
    ) -> None:
        """Bind verifier, durable principal repository, and exact audience."""
        self._verifier = verifier
        self._principals = principals
        self._audience = audience

    async def authorize(
        self, token: SecretStr, required_scope: Scope
    ) -> AuthorizedService:
        """Authorize the required scope only after both verification layers."""
        now = datetime.now(UTC)
        try:
            claims = self._verifier.verify(
                VerifyTokenRequest(
                    token=token.get_secret_value(),
                    audience=self._audience,
                    required_scopes=frozenset({required_scope}),
                    now=now,
                )
            )
        except TokenRejectedError as error:
            raise _invalid_token() from error
        decision = await self._principals.authorize(
            PrincipalAuthorizationRequest(
                principal_id=claims.subject,
                credential_version=claims.credential_version,
                jwt_id=claims.jwt_id,
                checked_at=now,
            )
        )
        if not decision.authorized:
            raise _invalid_token()
        return AuthorizedService(claims.subject, frozenset(claims.scopes))


def _invalid_token() -> IdentityError:
    return IdentityError(IdentityErrorCode.INVALID_TOKEN, "service token rejected")


__all__ = ("SqlScopeAuthorizer",)
