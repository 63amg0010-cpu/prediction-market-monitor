# ruff: noqa: INP001
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.jwt import (
    Ed25519TokenIssuer,
    Ed25519TokenVerifier,
    IssueTokenRequest,
    PrincipalCredentialState,
    TokenRejectedError,
    VerifyTokenRequest,
    enforce_credential_state,
)
from app.core.principals import (
    CredentialVersion,
    Principal,
    PrincipalId,
    PrincipalKind,
    Scope,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)


def _token_pair() -> tuple[Ed25519TokenIssuer, Ed25519TokenVerifier]:
    private_key = Ed25519PrivateKey.generate()
    return (
        Ed25519TokenIssuer(
            issuer="monitor-api",
            active_key_id="key-1",
            private_key=private_key,
        ),
        Ed25519TokenVerifier(
            issuer="monitor-api",
            public_keys={"key-1": private_key.public_key()},
        ),
    )


def _issue_request(now: datetime) -> IssueTokenRequest:
    return IssueTokenRequest(
        principal=Principal(
            id=PrincipalId("bff-production"),
            kind=PrincipalKind.BFF,
            credential_version=CredentialVersion("bff-v1"),
        ),
        audience="https://api.example.test",
        scopes=frozenset({Scope.BFF_AUTH, Scope.BFF_READ}),
        now=now,
        lifetime=timedelta(minutes=5),
    )


def test_jwt_round_trip_preserves_required_claims() -> None:
    # Given
    now = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    issuer, verifier = _token_pair()
    token = issuer.issue(_issue_request(now))

    # When
    claims = verifier.verify(
        VerifyTokenRequest(
            token=token,
            audience="https://api.example.test",
            required_scopes=frozenset({Scope.BFF_READ}),
            now=now + timedelta(seconds=1),
        )
    )

    # Then
    assert claims.issuer == "monitor-api"
    assert claims.audience == "https://api.example.test"
    assert claims.credential_version == CredentialVersion("bff-v1")
    assert claims.jwt_id
    assert claims.issued_at == int(now.timestamp())


def test_jwt_enforces_credential_rotation_and_principal_revocation() -> None:
    # Given
    now = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    issuer, verifier = _token_pair()
    claims = verifier.verify(
        VerifyTokenRequest(
            token=issuer.issue(_issue_request(now)),
            audience="https://api.example.test",
            required_scopes=frozenset({Scope.BFF_READ}),
            now=now,
        )
    )

    # When / Then
    enforce_credential_state(
        claims,
        PrincipalCredentialState(
            principal_id=PrincipalId("bff-production"),
            active_versions=frozenset({CredentialVersion("bff-v1")}),
            revoked=False,
        ),
    )
    with pytest.raises(TokenRejectedError):
        enforce_credential_state(
            claims,
            PrincipalCredentialState(
                principal_id=PrincipalId("bff-production"),
                active_versions=frozenset({CredentialVersion("bff-v2")}),
                revoked=False,
            ),
        )
    with pytest.raises(TokenRejectedError):
        enforce_credential_state(
            claims,
            PrincipalCredentialState(
                principal_id=PrincipalId("bff-production"),
                active_versions=frozenset({CredentialVersion("bff-v1")}),
                revoked=True,
            ),
        )


@pytest.mark.parametrize(
    ("audience", "required_scopes", "elapsed"),
    [
        ("https://other.example.test", frozenset({Scope.BFF_READ}), 1),
        ("https://api.example.test", frozenset({Scope.ADMIN_COMMAND}), 1),
        ("https://api.example.test", frozenset({Scope.BFF_READ}), 331),
    ],
)
def test_jwt_rejects_wrong_audience_scope_or_expiry(
    audience: str,
    required_scopes: frozenset[Scope],
    elapsed: int,
) -> None:
    # Given
    now = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    issuer, verifier = _token_pair()
    token = issuer.issue(_issue_request(now))

    # When / Then
    with pytest.raises(TokenRejectedError):
        _ = verifier.verify(
            VerifyTokenRequest(
                token=token,
                audience=audience,
                required_scopes=required_scopes,
                now=now + timedelta(seconds=elapsed),
            )
        )


def test_jwt_rejects_unknown_key_id() -> None:
    # Given
    now = datetime(2026, 7, 21, 4, 0, tzinfo=UTC)
    issuer, _ = _token_pair()
    unrelated_key = Ed25519PrivateKey.generate()
    verifier = Ed25519TokenVerifier(
        issuer="monitor-api",
        public_keys={"different-key": unrelated_key.public_key()},
    )

    # When / Then
    with pytest.raises(TokenRejectedError):
        _ = verifier.verify(
            VerifyTokenRequest(
                token=issuer.issue(_issue_request(now)),
                audience="https://api.example.test",
                required_scopes=frozenset({Scope.BFF_READ}),
                now=now,
            )
        )
