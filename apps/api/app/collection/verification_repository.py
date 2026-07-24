"""PostgreSQL-backed freshness snapshot and observation handler."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Never, final

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .verification_observation_store import record_observations
from .verification_snapshot_identity import (
    SnapshotIntegrityError,
    snapshot_response,
)
from .verification_snapshot_store import (
    canonical_snapshot,
    database_now,
    load_snapshot,
    observation_facts,
    persist_snapshot,
    source_facts,
)

if TYPE_CHECKING:
    from app.api.routes.verification import (
        ObservationAccepted,
        VerificationObservationPayload,
        VerificationSnapshot,
    )
    from app.db.session import DatabaseSessions

_READ_SNAPSHOT: Final = text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
_WRITE_SNAPSHOT: Final = text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
_DUPLICATE_SQLSTATE: Final = "23505"


@final
class SqlAlchemyVerificationRepository:
    """Read consistent evidence and persist server-derived S/C/P facts."""

    def __init__(self, sessions: DatabaseSessions, scope_version: str) -> None:
        """Bind one configured scope to an explicit database session owner."""
        self._sessions = sessions
        self._scope_version = scope_version

    async def snapshot(self) -> VerificationSnapshot:
        """Return a repeatable-read snapshot with a canonical evidence hash."""
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(_READ_SNAPSHOT)
            published_at = await database_now(session)
            facts = await source_facts(session, self._scope_version, published_at)
            envelope = canonical_snapshot(self._scope_version, published_at, facts)
            persist_snapshot(session, envelope)
        return snapshot_response(envelope)

    async def record(
        self, payload: VerificationObservationPayload
    ) -> ObservationAccepted:
        """Validate snapshot identity and atomically write authoritative facts."""
        if payload.scope_version != self._scope_version:
            raise HTTPException(status_code=409)
        try:
            async with self._sessions.open() as session, session.begin():
                _ = await session.execute(_WRITE_SNAPSHOT)
                observed_at = await database_now(session)
                try:
                    snapshot = await load_snapshot(
                        session,
                        payload.snapshot_id,
                        self._scope_version,
                        payload.snapshot_checksum,
                    )
                except SnapshotIntegrityError as error:
                    raise HTTPException(status_code=409) from error
                if snapshot.evidence.published_at > observed_at:
                    raise HTTPException(status_code=409)
                try:
                    facts = await observation_facts(session, snapshot)
                except SnapshotIntegrityError as error:
                    raise HTTPException(status_code=409) from error
                accepted = await record_observations(
                    session, snapshot, facts, payload, observed_at
                )
                await session.flush()
        except IntegrityError as error:
            raise_verification_integrity_error(error)
        return accepted


def raise_verification_integrity_error(error: IntegrityError) -> Never:
    """Translate a PostgreSQL uniqueness race without masking other DB failures."""
    if _is_unique_violation(error):
        raise HTTPException(status_code=409) from error
    raise error


def _is_unique_violation(error: IntegrityError) -> bool:
    """Recognize PostgreSQL duplicate races without masking other integrity bugs."""
    current: BaseException | None = error.orig
    while current is not None:
        if (
            getattr(current, "sqlstate", None) == _DUPLICATE_SQLSTATE
            or getattr(current, "pgcode", None) == _DUPLICATE_SQLSTATE
        ):
            return True
        current = current.__cause__
    return False


__all__ = (
    "SqlAlchemyVerificationRepository",
    "raise_verification_integrity_error",
)
