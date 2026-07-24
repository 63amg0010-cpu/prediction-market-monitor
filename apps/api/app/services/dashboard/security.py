"""Shared fail-closed bearer parsing for API route factories."""

from pydantic import SecretStr

from app.core.errors import IdentityError, IdentityErrorCode
from app.core.principals import Scope

from .models import AuthorizedService
from .ports import ScopeAuthorizer


def bearer_token(authorization: str | None) -> SecretStr:
    """Parse the exact Bearer scheme without accepting an empty token."""
    if authorization is None or not authorization.startswith("Bearer "):
        raise IdentityError(IdentityErrorCode.INVALID_TOKEN, "service token required")
    value = authorization.removeprefix("Bearer ")
    if not value:
        raise IdentityError(IdentityErrorCode.INVALID_TOKEN, "service token required")
    return SecretStr(value)


async def require_scope(
    authorizer: ScopeAuthorizer,
    authorization: str | None,
    scope: Scope,
) -> AuthorizedService:
    """Authorize one parsed token for one exact route scope."""
    return await authorizer.authorize(bearer_token(authorization), scope)
