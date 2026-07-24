"""Fail-closed identity adapters for unimplemented durable prerequisites."""

# ruff: noqa: D102 - Method contracts are documented by their route Protocols.

from __future__ import annotations

from typing import TYPE_CHECKING, Never

from anyio.lowlevel import checkpoint

from app.core.errors import IdentityError, IdentityErrorCode

if TYPE_CHECKING:
    from pydantic import SecretStr

    from app.api.routes.auth import (
        AdminSessionResponse,
        LoginCommand,
        LogoutCommand,
        SessionCommand,
    )
    from app.api.routes.commands import AdminCommandContext
    from app.core.principals import Scope
    from app.services.dashboard.models import AuthorizedService
    from app.services.identity.exchanges import (
        BffExchangeCommand,
        BffExchangeResponse,
        GitHubExchangeCommand,
        WorkerExchangeCommand,
    )


def raise_unavailable() -> Never:
    """Raise the common redacted unavailable boundary error."""
    raise IdentityError(
        IdentityErrorCode.SERVICE_UNAVAILABLE,
        "service dependency is unavailable",
    )


class UnavailableAuthHandler:
    """Reject administrator session work until durable stores are composed."""

    async def login(self, command: LoginCommand) -> AdminSessionResponse:
        del command
        await checkpoint()
        raise_unavailable()

    async def session(self, command: SessionCommand) -> AdminSessionResponse:
        del command
        await checkpoint()
        raise_unavailable()

    async def logout(self, command: LogoutCommand) -> None:
        del command
        await checkpoint()
        raise_unavailable()


class UnavailableServiceTokenHandler:
    """Reject token exchanges until every external identity adapter exists."""

    async def exchange_bff(self, command: BffExchangeCommand) -> BffExchangeResponse:
        del command
        await checkpoint()
        raise_unavailable()

    async def exchange_github(
        self, command: GitHubExchangeCommand
    ) -> BffExchangeResponse:
        del command
        await checkpoint()
        raise_unavailable()

    async def exchange_worker(
        self, command: WorkerExchangeCommand
    ) -> BffExchangeResponse:
        del command
        await checkpoint()
        raise_unavailable()


class UnavailableScopeAuthorizer:
    """Reject bearer tokens until signature and durable state checks are wired."""

    async def authorize(
        self, token: SecretStr, required_scope: Scope
    ) -> AuthorizedService:
        del token, required_scope
        await checkpoint()
        raise_unavailable()


class UnavailableAdminMutationAuthorizer:
    """Reject mutations until the complete administrator guard is durable."""

    async def authorize(self, context: AdminCommandContext) -> None:
        del context
        await checkpoint()
        raise_unavailable()


__all__ = [
    "UnavailableAdminMutationAuthorizer",
    "UnavailableAuthHandler",
    "UnavailableScopeAuthorizer",
    "UnavailableServiceTokenHandler",
    "raise_unavailable",
]
