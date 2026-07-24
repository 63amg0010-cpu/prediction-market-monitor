# ruff: noqa: INP001
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.errors import IdentityError, IdentityErrorCode
from app.core.principals import CredentialVersion
from app.services.identity.admin import AdminPasswordVerifier
from app.services.identity.sessions import (
    AdminSessionRecord,
    CsrfProtector,
    CsrfVerificationRequest,
    SessionEvaluationRequest,
    SessionId,
    SessionState,
    evaluate_session,
    hash_session_token,
)
from argon2 import PasswordHasher
from pydantic import SecretBytes, SecretStr


def test_argon2id_password_verification() -> None:
    # Given
    plain_text = "correct horse battery staple"
    encoded_hash = PasswordHasher().hash(plain_text)
    verifier = AdminPasswordVerifier(SecretStr(encoded_hash))

    # When / Then
    assert verifier.verify(SecretStr(plain_text))
    assert not verifier.verify(SecretStr("wrong password"))


def _session(now: datetime) -> tuple[AdminSessionRecord, SecretStr]:
    token = SecretStr("s" * 43)
    return (
        AdminSessionRecord(
            id=SessionId("session-1"),
            token_digest=hash_session_token(token),
            csrf_seed=SecretBytes(b"z" * 32),
            credential_version=CredentialVersion("admin-v1"),
            issued_at=now,
            rotate_at=now + timedelta(hours=4),
            expires_at=now + timedelta(hours=8),
            state=SessionState.ACTIVE,
        ),
        token,
    )


def test_session_rotates_at_four_hours_and_rejects_revocation() -> None:
    # Given
    now = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    record, token = _session(now)

    # When
    result = evaluate_session(
        SessionEvaluationRequest(
            record=record,
            presented_token=token,
            now=now + timedelta(hours=4),
            active_versions=frozenset({CredentialVersion("admin-v1")}),
        )
    )

    # Then
    assert result.rotation_required

    # Given revoked state
    revoked = record.with_state(SessionState.REVOKED)

    # When / Then
    with pytest.raises(IdentityError) as raised:
        _ = evaluate_session(
            SessionEvaluationRequest(
                record=revoked,
                presented_token=token,
                now=now,
                active_versions=frozenset({CredentialVersion("admin-v1")}),
            )
        )
    assert raised.value.code is IdentityErrorCode.SESSION_REVOKED


def test_csrf_accepts_current_and_prior_bucket_for_allowed_origin() -> None:
    # Given
    now = datetime(2026, 7, 21, 4, 16, tzinfo=UTC)
    record, _ = _session(now)
    protector = CsrfProtector(
        signing_secret=SecretBytes(b"h" * 32),
        allowed_origins=frozenset({"https://dashboard.example.test"}),
    )
    current = protector.issue(record, now)
    prior = protector.issue(record, now - timedelta(minutes=15))

    # When / Then
    protector.verify(
        CsrfVerificationRequest(
            record=record,
            token=current,
            origin="https://dashboard.example.test",
            referer=None,
            now=now,
        )
    )
    protector.verify(
        CsrfVerificationRequest(
            record=record,
            token=prior,
            origin=None,
            referer="https://dashboard.example.test/admin",
            now=now,
        )
    )

    with pytest.raises(IdentityError) as raised:
        protector.verify(
            CsrfVerificationRequest(
                record=record,
                token=current,
                origin="https://evil.example.test",
                referer=None,
                now=now,
            )
        )
    assert raised.value.code is IdentityErrorCode.CSRF_REJECTED
