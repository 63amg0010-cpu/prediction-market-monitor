"""Atomic PostgreSQL login-failure limits with one-way client identities."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, final
from uuid import uuid4

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.auth_models import LoginRateLimit

from .admin import LOGIN_FAILURE_LIMIT, LOGIN_FAILURE_WINDOW_SECONDS

if TYPE_CHECKING:
    from pydantic import SecretBytes

    from app.db.session import DatabaseSessions

_CLIENT_HASH_DOMAIN = b"admin-login-client-v1\x00"


@final
class SqlLoginFailureRepository:
    """Enforce the reviewed per-client failure bucket under PostgreSQL locks."""

    def __init__(self, sessions: DatabaseSessions, secret: SecretBytes) -> None:
        """Bind the durable store and keyed client-identity material."""
        self._sessions = sessions
        self._secret = secret

    async def is_allowed(self, client_ip: str, now: datetime) -> bool:
        """Return false while the current failure bucket is exhausted."""
        client_hash = self._client_hash(client_ip)
        bucket_start, _ = _bucket(now)
        async with self._sessions.open() as session, session.begin():
            row = await session.scalar(
                select(LoginRateLimit).where(
                    LoginRateLimit.client_hash == client_hash,
                    LoginRateLimit.bucket_start == bucket_start,
                )
            )
        return row is None or (
            row.failure_count < LOGIN_FAILURE_LIMIT
            and (row.locked_until is None or row.locked_until <= now)
        )

    async def record_failure(self, client_ip: str, now: datetime) -> bool:
        """Atomically increment one bucket and return whether attempts remain."""
        client_hash = self._client_hash(client_ip)
        bucket_start, bucket_end = _bucket(now)
        next_count = LoginRateLimit.failure_count + 1
        statement = (
            insert(LoginRateLimit)
            .values(
                id=uuid4(),
                client_hash=client_hash,
                bucket_start=bucket_start,
                bucket_end=bucket_end,
                failure_count=1,
                locked_until=None,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=["client_hash", "bucket_start"],
                set_={
                    "failure_count": next_count,
                    "locked_until": case(
                        (next_count >= LOGIN_FAILURE_LIMIT, bucket_end),
                        else_=LoginRateLimit.locked_until,
                    ),
                    "updated_at": now,
                },
            )
            .returning(LoginRateLimit.failure_count)
        )
        async with self._sessions.open() as session, session.begin():
            failure_count = (await session.execute(statement)).scalar_one()
        return failure_count < LOGIN_FAILURE_LIMIT

    async def clear(self, client_ip: str) -> None:
        """Delete one client's keyed failure history after successful login."""
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(
                delete(LoginRateLimit).where(
                    LoginRateLimit.client_hash == self._client_hash(client_ip)
                )
            )

    def _client_hash(self, client_ip: str) -> bytes:
        return hmac.new(
            self._secret.get_secret_value(),
            _CLIENT_HASH_DOMAIN + client_ip.encode(),
            hashlib.sha256,
        ).digest()


def _bucket(now: datetime) -> tuple[datetime, datetime]:
    timestamp = int(now.timestamp())
    start = timestamp - (timestamp % LOGIN_FAILURE_WINDOW_SECONDS)
    bucket_start = datetime.fromtimestamp(start, tz=UTC)
    return bucket_start, bucket_start + timedelta(seconds=LOGIN_FAILURE_WINDOW_SECONDS)
