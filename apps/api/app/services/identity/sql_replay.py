"""PostgreSQL replay and fixed-window rate-limit repositories."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from math import ceil
from typing import TYPE_CHECKING, final

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.db.auth_models import LoginRateLimit, OneUseNonce
from app.domain.enums import NoncePurpose

from .ports import RateLimitDecision, RateLimitRule

if TYPE_CHECKING:
    from app.db.session import DatabaseSessions

_PURPOSES = {
    "github-oidc": NoncePurpose.GITHUB_EXCHANGE,
    "worker-bootstrap": NoncePurpose.WORKER_EXCHANGE,
    "bff": NoncePurpose.BFF_EXCHANGE,
}


@final
class SqlNonceRepository:
    """Consume namespaced nonces atomically until their retention expires."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        """Bind nonce operations to the production session owner."""
        self._sessions = sessions

    async def consume_once(
        self, namespace: str, key: str, retain_until: datetime
    ) -> bool:
        """Consume a known namespace and reject duplicates atomically."""
        purpose = _PURPOSES.get(namespace)
        if purpose is None:
            return False
        digest = sha256(f"{namespace}\0{key}".encode()).digest()
        async with self._sessions.open() as session, session.begin():
            statement = (
                insert(OneUseNonce)
                .values(
                    purpose=purpose,
                    nonce_hash=digest,
                    external_identity=key[:300],
                    expires_at=retain_until,
                    used_at=func.clock_timestamp(),
                )
                .on_conflict_do_update(
                    constraint="uq_nonce_purpose_hash",
                    set_={
                        "external_identity": key[:300],
                        "expires_at": retain_until,
                        "used_at": func.clock_timestamp(),
                    },
                    where=OneUseNonce.expires_at <= func.clock_timestamp(),
                )
                .returning(OneUseNonce.id)
            )
            return (await session.execute(statement)).scalar_one_or_none() is not None


@final
class SqlRateLimitRepository:
    """Atomically increment deployment-keyed fixed-window counters."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        """Bind rate-limit operations to the production session owner."""
        self._sessions = sessions

    async def consume(
        self, key: str, rule: RateLimitRule, now: datetime
    ) -> RateLimitDecision:
        """Consume one allowance and return the exact window retry delay."""
        timestamp = int(now.timestamp())
        start_seconds = timestamp - timestamp % rule.window_seconds
        start = datetime.fromtimestamp(start_seconds, tz=UTC)
        end = start + timedelta(seconds=rule.window_seconds)
        client_hash = sha256(f"{rule.bucket}\0{key}".encode()).digest()
        async with self._sessions.open() as session, session.begin():
            statement = (
                insert(LoginRateLimit)
                .values(
                    client_hash=client_hash,
                    bucket_start=start,
                    bucket_end=end,
                    failure_count=1,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    constraint="uq_login_bucket",
                    set_={
                        "failure_count": LoginRateLimit.failure_count + 1,
                        "updated_at": now,
                    },
                )
                .returning(LoginRateLimit.failure_count)
            )
            count = (await session.execute(statement)).scalar_one()
        retry_after = max(1, ceil((end - now).total_seconds()))
        return RateLimitDecision(count <= rule.limit, retry_after)


__all__ = ("SqlNonceRepository", "SqlRateLimitRepository")
