"""Production administrator auth and mutation authorization composition."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from app.api.routes.auth import (
    AdminSessionResponse,
    LoginCommand,
    LogoutCommand,
    SessionCommand,
)
from app.core.errors import IdentityError, IdentityErrorCode
from app.core.principals import Scope

from .admin import LOGIN_FAILURE_WINDOW_SECONDS, AdminPasswordVerifier
from .exchanges import SystemClock

if TYPE_CHECKING:
    from app.api.routes.commands import AdminCommandContext
    from app.services.dashboard.ports import ScopeAuthorizer

    from .admin import LoginFailureRepository
    from .sql_admin_sessions import SqlAdminSessionStore


@final
class SqlAdminHandlers:
    """Combine scoped BFF identity with durable session and CSRF checks."""

    def __init__(
        self,
        *,
        scopes: ScopeAuthorizer,
        password: AdminPasswordVerifier,
        failures: LoginFailureRepository,
        sessions: SqlAdminSessionStore,
        clock: SystemClock | None = None,
    ) -> None:
        """Bind all required administrator authority boundaries."""
        self._scopes = scopes
        self._password = password
        self._failures = failures
        self._sessions = sessions
        self._clock = clock or SystemClock()

    async def login(self, command: LoginCommand) -> AdminSessionResponse:
        """Authorize BFF, rate-limit password checks, and create a session."""
        _ = await self._scopes.authorize(command.bff_token, Scope.BFF_AUTH)
        now = self._clock.now()
        if not await self._failures.is_allowed(command.client_ip, now):
            raise _rate_limited()
        if not self._password.verify(command.password):
            still_allowed = await self._failures.record_failure(
                command.client_ip,
                now,
            )
            if not still_allowed:
                raise _rate_limited()
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "administrator credentials rejected",
            )
        await self._failures.clear(command.client_ip)
        issued = await self._sessions.create(now)
        return AdminSessionResponse(
            session_token=issued.token.get_secret_value(),
            expires_at=issued.record.expires_at,
            csrf_token=self._sessions.csrf_token(issued.record, now),
            rotated=False,
        )

    async def session(self, command: SessionCommand) -> AdminSessionResponse:
        """Authorize BFF and rotate an eligible opaque session under lock."""
        _ = await self._scopes.authorize(command.bff_token, Scope.BFF_AUTH)
        now = self._clock.now()
        access = await self._sessions.access(
            command.session_token,
            now,
            rotate=True,
        )
        replacement = access.replacement_token
        return AdminSessionResponse(
            session_token=(
                None if replacement is None else replacement.get_secret_value()
            ),
            expires_at=access.record.expires_at,
            csrf_token=self._sessions.csrf_token(access.record, now),
            rotated=replacement is not None,
        )

    async def logout(self, command: LogoutCommand) -> None:
        """Authorize BFF, verify same-origin CSRF, and revoke the session."""
        _ = await self._scopes.authorize(command.bff_token, Scope.BFF_AUTH)
        await self._sessions.revoke(
            command.session_token,
            command.csrf_token,
            command.origin,
            command.referer,
            self._clock.now(),
        )

    async def authorize(self, context: AdminCommandContext) -> None:
        """Require admin scope, active session, and same-origin CSRF together."""
        _ = await self._scopes.authorize(
            context.bff_token,
            Scope.ADMIN_COMMAND,
        )
        _ = await self._sessions.verify_csrf(
            context.session_token,
            context.csrf_token,
            context.origin,
            context.referer,
            self._clock.now(),
        )


def _rate_limited() -> IdentityError:
    return IdentityError(
        IdentityErrorCode.RATE_LIMITED,
        "administrator login rate limit exceeded",
        LOGIN_FAILURE_WINDOW_SECONDS,
    )
