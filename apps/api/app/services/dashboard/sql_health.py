"""Redacted SQLAlchemy database availability probe."""

from __future__ import annotations

from typing import TYPE_CHECKING, final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .models import DatabaseStatus

if TYPE_CHECKING:
    from app.db.session import DatabaseSessions


@final
class SqlAlchemyHealthProbe:
    """Probe configured sessions while reducing failures to an allowlisted state."""

    def __init__(self, sessions: DatabaseSessions | None) -> None:
        """Accept None as an explicitly unavailable fail-closed composition."""
        self._sessions = sessions

    async def database_status(self) -> DatabaseStatus:
        """Execute a minimal query without exposing connection failure details."""
        if self._sessions is None:
            return DatabaseStatus.UNAVAILABLE
        try:
            async with self._sessions.open() as session:
                _ = await session.execute(text("SELECT 1"))
        except (OSError, TimeoutError, SQLAlchemyError):
            return DatabaseStatus.UNAVAILABLE
        return DatabaseStatus.OK
