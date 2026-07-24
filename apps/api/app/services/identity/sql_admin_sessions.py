"""PostgreSQL adapter for opaque administrator sessions and CSRF state."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, final
from uuid import UUID

from pydantic import SecretBytes, SecretStr
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.errors import IdentityError, IdentityErrorCode
from app.db.auth_models import AdminSession, ServicePrincipal
from app.domain.enums import PrincipalKind

from .sessions import (
    SESSION_ROTATION_AGE,
    AdminSessionRecord,
    CsrfProtector,
    CsrfVerificationRequest,
    IssuedAdminSession,
    SessionEvaluationRequest,
    SessionId,
    SessionState,
    SessionTokenDigest,
    evaluate_session,
    hash_session_token,
    issue_admin_session,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.principals import CredentialVersion
    from app.db.session import DatabaseSessions

_ADMIN_SUBJECT = "single-admin"
_CSRF_SEED_DOMAIN = b"admin-csrf-seed-v1\x00"


@dataclass(frozen=True, slots=True)
class AdminSessionAccess:
    """Validated session with optional one-time replacement token."""

    record: AdminSessionRecord
    replacement_token: SecretStr | None


@final
class SqlAdminSessionStore:
    """Persist only digests while evaluating and rotating sessions under lock."""

    def __init__(
        self,
        sessions: DatabaseSessions,
        *,
        signing_secret: SecretBytes,
        credential_version: CredentialVersion,
        allowed_origins: frozenset[str],
    ) -> None:
        """Bind database state to one active credential and origin policy."""
        self._sessions = sessions
        self._secret = signing_secret
        self._version = credential_version
        self._active_versions = frozenset({credential_version})
        self._csrf = CsrfProtector(
            signing_secret=signing_secret,
            allowed_origins=allowed_origins,
        )

    async def create(self, now: datetime) -> IssuedAdminSession:
        """Create one opaque session without storing its plaintext token."""
        async with self._sessions.open() as session, session.begin():
            return await self._create_in_session(session, now)

    async def access(
        self,
        token: SecretStr,
        now: datetime,
        *,
        rotate: bool,
    ) -> AdminSessionAccess:
        """Evaluate a locked session and optionally rotate its token."""
        async with self._sessions.open() as session, session.begin():
            row = await self._locked_row(session, hash_session_token(token))
            record = self._record(row)
            evaluation = evaluate_session(
                SessionEvaluationRequest(
                    record=record,
                    presented_token=token,
                    now=now,
                    active_versions=self._active_versions,
                )
            )
            if not evaluation.rotation_required or not rotate:
                return AdminSessionAccess(record, None)
            replacement = await self._create_in_session(session, now)
            row.revoked_at = now
            row.rotated_at = now
            return AdminSessionAccess(replacement.record, replacement.token)

    async def verify_csrf(
        self,
        token: SecretStr,
        csrf_token: str,
        origin: str | None,
        referer: str | None,
        now: datetime,
    ) -> AdminSessionRecord:
        """Evaluate session and same-origin CSRF evidence under one row lock."""
        async with self._sessions.open() as session, session.begin():
            row = await self._locked_row(session, hash_session_token(token))
            record = self._record(row)
            _ = evaluate_session(
                SessionEvaluationRequest(
                    record=record,
                    presented_token=token,
                    now=now,
                    active_versions=self._active_versions,
                )
            )
            self._csrf.verify(
                CsrfVerificationRequest(
                    record=record,
                    token=csrf_token,
                    origin=origin,
                    referer=referer,
                    now=now,
                )
            )
            return record

    async def revoke(
        self,
        token: SecretStr,
        csrf_token: str,
        origin: str | None,
        referer: str | None,
        now: datetime,
    ) -> None:
        """Verify session and CSRF before durable revocation."""
        async with self._sessions.open() as session, session.begin():
            row = await self._locked_row(session, hash_session_token(token))
            record = self._record(row)
            _ = evaluate_session(
                SessionEvaluationRequest(
                    record=record,
                    presented_token=token,
                    now=now,
                    active_versions=self._active_versions,
                )
            )
            self._csrf.verify(
                CsrfVerificationRequest(
                    record=record,
                    token=csrf_token,
                    origin=origin,
                    referer=referer,
                    now=now,
                )
            )
            row.revoked_at = now

    def csrf_token(self, record: AdminSessionRecord, now: datetime) -> str:
        """Issue the current origin-bound CSRF token for a validated session."""
        return self._csrf.issue(record, now)

    async def _create_in_session(
        self,
        session: AsyncSession,
        now: datetime,
    ) -> IssuedAdminSession:
        principal_id = await self._principal_id(session, now)
        issued = issue_admin_session(now, self._version)
        session_id = UUID(issued.record.id)
        seed = self._csrf_seed(session_id, issued.record.token_digest)
        record = replace(issued.record, csrf_seed=SecretBytes(seed))
        session.add(
            AdminSession(
                id=session_id,
                principal_id=principal_id,
                session_token_hash=bytes(record.token_digest),
                csrf_current_hash=hashlib.sha256(seed).digest(),
                csrf_prior_hash=None,
                expires_at=record.expires_at,
                rotated_at=None,
                revoked_at=None,
                created_at=now,
            )
        )
        return IssuedAdminSession(issued.token, record)

    async def _principal_id(
        self,
        session: AsyncSession,
        now: datetime,
    ) -> UUID:
        _ = await session.execute(
            insert(ServicePrincipal)
            .values(
                kind=PrincipalKind.ADMIN,
                subject=_ADMIN_SUBJECT,
                active=True,
                created_at=now,
            )
            .on_conflict_do_nothing(index_elements=["kind", "subject"])
        )
        principal_id = await session.scalar(
            select(ServicePrincipal.id).where(
                ServicePrincipal.kind == PrincipalKind.ADMIN,
                ServicePrincipal.subject == _ADMIN_SUBJECT,
                ServicePrincipal.active.is_(True),
                ServicePrincipal.revoked_at.is_(None),
            )
        )
        if principal_id is None:
            raise IdentityError(
                IdentityErrorCode.SERVICE_UNAVAILABLE,
                "administrator identity unavailable",
            )
        return principal_id

    async def _locked_row(
        self,
        session: AsyncSession,
        digest: SessionTokenDigest,
    ) -> AdminSession:
        row = await session.scalar(
            select(AdminSession)
            .where(AdminSession.session_token_hash == bytes(digest))
            .with_for_update()
        )
        if row is None:
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "administrator session rejected",
            )
        return row

    def _record(self, row: AdminSession) -> AdminSessionRecord:
        session_id = SessionId(str(row.id))
        digest = SessionTokenDigest(row.session_token_hash)
        state = (
            SessionState.REVOKED if row.revoked_at is not None else SessionState.ACTIVE
        )
        return AdminSessionRecord(
            id=session_id,
            token_digest=digest,
            csrf_seed=SecretBytes(self._csrf_seed(row.id, digest)),
            credential_version=self._version,
            issued_at=row.created_at,
            rotate_at=row.created_at + SESSION_ROTATION_AGE,
            expires_at=row.expires_at,
            state=state,
        )

    def _csrf_seed(self, session_id: UUID, digest: SessionTokenDigest) -> bytes:
        payload = _CSRF_SEED_DOMAIN + session_id.bytes + bytes(digest)
        return hmac.new(
            self._secret.get_secret_value(),
            payload,
            hashlib.sha256,
        ).digest()
