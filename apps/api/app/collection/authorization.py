"""Fail-closed source authorization evaluation."""

from dataclasses import dataclass
from datetime import datetime
from typing import assert_never
from uuid import UUID

from app.domain.enums import AuthorizationStatus

from .base import CollectionError, CollectionErrorCode, require_utc


@dataclass(frozen=True, slots=True)
class AuthorizationSnapshot:
    """Persisted authorization facts locked for a collection operation."""

    decision_id: UUID
    source_id: UUID
    scope_version: str
    enabled: bool
    status: AuthorizationStatus
    effective_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class ActiveAuthorization:
    """Proof that one exact source scope passed the runtime gate."""

    decision_id: UUID


def require_active_authorization(
    snapshot: AuthorizationSnapshot,
    source_id: UUID,
    scope_version: str,
    db_now: datetime,
) -> ActiveAuthorization:
    """Return active proof only for a current exact-scope approval."""
    now = require_utc(db_now)
    match snapshot.status:
        case AuthorizationStatus.APPROVED:
            approved = True
        case (
            AuthorizationStatus.DENIED
            | AuthorizationStatus.REVOKED
            | AuthorizationStatus.EXPIRED
        ):
            approved = False
        case _:
            assert_never(snapshot.status)
    scope_matches = (
        snapshot.source_id == source_id and snapshot.scope_version == scope_version
    )
    time_valid = (
        snapshot.effective_at <= now
        and snapshot.expires_at is not None
        and snapshot.expires_at > now
        and snapshot.revoked_at is None
    )
    if not (snapshot.enabled and approved and scope_matches and time_valid):
        raise CollectionError(
            CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE,
            403,
        )
    return ActiveAuthorization(snapshot.decision_id)
