import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest
from app.api.routes.activation_evidence import (
    RESERVATION_READ_SQL,
    ActivationEvidenceOidcAuthorizer,
    SqlActivationEvidenceVerifier,
)
from app.core.errors import IdentityError
from app.core.jwt import TOKEN_SKEW_SECONDS
from app.main import AppDependencies, create_app
from app.services.identity.github import GitHubOIDCClaims
from fastapi.testclient import TestClient
from pydantic import SecretStr
from scripts.activation_evidence_models import (
    ActivationEvidenceReceipt,
    ActivationEvidenceVerifyRequest,
    PublicActivationAttestation,
    canonical_attestation_bytes,
    canonical_evidence_receipt_bytes,
)

NOW = datetime(2026, 7, 28, 11, tzinfo=UTC)
REPOSITORY = "63amg0010-cpu/prediction-market-monitor"
RAW_OIDC_TOKEN = "raw.github.oidc"  # noqa: S105 - Deliberate inert test JWT.


@dataclass(frozen=True, slots=True)
class _FixedClock:
    value: datetime

    def now(self) -> datetime:
        return self.value


class _RecordingOidcVerifier:
    def __init__(self, claims: GitHubOIDCClaims) -> None:
        self.claims: GitHubOIDCClaims = claims
        self.tokens: list[str] = []

    async def verify(
        self, token: SecretStr, now: datetime
    ) -> GitHubOIDCClaims:
        assert now == NOW
        self.tokens.append(token.get_secret_value())
        return self.claims


def _attestation() -> PublicActivationAttestation:
    return PublicActivationAttestation.model_validate(
        {
            "schema_version": 1,
            "reviewed_sha": "a" * 40,
            "activation_nonce": "11111111-1111-4111-8111-111111111111",
            "attestation_generation": 1,
            "source_scope_version": "reviewed-manifold-v1",
            "authorization_evidence_sha256": "b" * 64,
            "free_tier_evidence_sha256": "c" * 64,
            "provenance_sha256": "d" * 64,
            "predecessor_attestation_sha256": None,
            "captured_at": "2026-07-28T10:00:00Z",
            "evidence_database_time": "2026-07-28T10:00:00Z",
            "public_evidence_urls": ["https://example.com"],
        }
    )


def _payload() -> ActivationEvidenceVerifyRequest:
    attestation = _attestation()
    return ActivationEvidenceVerifyRequest(
        attestation=attestation,
        attestation_sha256=sha256(canonical_attestation_bytes(attestation)).hexdigest(),
        reservation_receipt_sha256="e" * 64,
        dispatch_nonce=UUID("22222222-2222-4222-8222-222222222222"),
        attempt=1,
        run_id=123,
        run_attempt=2,
        head_sha="a" * 40,
    )


def _claims() -> GitHubOIDCClaims:
    timestamp = int(NOW.timestamp())
    return GitHubOIDCClaims.model_validate(
        {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "monitor-control",
            "sub": (
                "repo:63amg0010-cpu@256795069/"
                "prediction-market-monitor@1310655558:"
                "environment:production-collector"
            ),
            "repository": REPOSITORY,
            "repository_id": "1310655558",
            "repository_owner_id": "256795069",
            "workflow_ref": (
                f"{REPOSITORY}/.github/workflows/"
                "activation-evidence.yml@refs/heads/main"
            ),
            "ref": "refs/heads/main",
            "sha": "a" * 40,
            "environment": "production-collector",
            "run_id": "123",
            "run_attempt": "2",
            "jti": "oidc-jti",
            "iat": timestamp,
            "nbf": timestamp,
            "exp": timestamp + 300,
        }
    )


def test_activation_evidence_endpoint_is_registered_before_0011() -> None:
    # Given: a fail-closed app with no release-evidence adapter.
    with TestClient(create_app(AppDependencies())) as client:
        # When: the protected workflow calls the exact activation-evidence path.
        response = client.post(
            "/internal/release/activation-evidence-verify",
            headers={"Authorization": "Bearer redacted.invalid"},
            json={},
        )

    # Then: the route exists and rejects the schema before any stateful operation.
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"


def test_activation_evidence_endpoint_accepts_complete_workflow_request_shape() -> None:
    # Given: a request shape built from all workflow-bound public evidence fields.
    activation_nonce = "11111111-1111-4111-8111-111111111111"
    dispatch_nonce = "22222222-2222-4222-8222-222222222222"
    request = {
        "attestation": {
            "schema_version": 1,
            "reviewed_sha": "a" * 40,
            "activation_nonce": activation_nonce,
            "attestation_generation": 1,
            "source_scope_version": "reviewed-manifold-v1",
            "authorization_evidence_sha256": "b" * 64,
            "free_tier_evidence_sha256": "c" * 64,
            "provenance_sha256": "d" * 64,
            "predecessor_attestation_sha256": None,
            "captured_at": "2026-07-28T00:00:00Z",
            "evidence_database_time": "2026-07-28T00:00:00Z",
            "public_evidence_urls": ["https://example.com/evidence"],
        },
        "attestation_sha256": "e" * 64,
        "reservation_receipt_sha256": "f" * 64,
        "dispatch_nonce": dispatch_nonce,
        "attempt": 1,
        "run_id": 123,
        "run_attempt": 1,
        "head_sha": "a" * 40,
    }

    with TestClient(create_app(AppDependencies())) as client:
        # When: the protected workflow submits the complete API model shape.
        response = client.post(
            "/internal/release/activation-evidence-verify",
            headers={"Authorization": "Bearer redacted.invalid"},
            json=request,
        )

    # Then: schema validation passes and only the unavailable verifier rejects it.
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


def test_activation_evidence_verifier_declares_read_only_repeatable_read() -> None:
    # Given: the production verifier's complete transaction implementation.
    implementation = inspect.getsource(SqlActivationEvidenceVerifier.verify)

    # When: its transaction and reservation SQL are inspected as one boundary.
    transaction_position = implementation.index(
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
    )
    reservation_position = implementation.index("RESERVATION_READ_SQL")

    # Then: read-only mode precedes a SELECT-only reservation lookup.
    assert transaction_position < reservation_position
    assert RESERVATION_READ_SQL.lstrip().startswith("SELECT")
    assert all(
        operation not in RESERVATION_READ_SQL.upper()
        for operation in ("INSERT ", "UPDATE ", "DELETE ", "ALTER ", "DROP ")
    )


def test_activation_evidence_reads_the_pre_0011_reservation_shape() -> None:
    # Given: activation evidence is reserved before the 0011 migration exists.
    normalized = " ".join(RESERVATION_READ_SQL.split())

    # Then: the read accepts only the activation workflow's revisionless reservation.
    assert "workflow_file = 'activation-evidence.yml'" in normalized
    assert "revision IS NULL" in normalized
    assert "revision = '20260727_0011'" not in normalized


@pytest.mark.asyncio
async def test_raw_github_oidc_token_is_authorized_directly() -> None:
    # Given: an exact activation-workflow claim set and a raw GitHub JWT.
    verifier = _RecordingOidcVerifier(_claims())
    authorizer = ActivationEvidenceOidcAuthorizer(
        verifier=verifier,
        clock=_FixedClock(NOW),
        repository=REPOSITORY,
    )

    # When: the endpoint-specific authorizer evaluates the request identity.
    claims = await authorizer.authorize(SecretStr(RAW_OIDC_TOKEN), _payload())

    # Then: the raw token went to GitHub OIDC verification, not service-token auth.
    assert verifier.tokens == [RAW_OIDC_TOKEN]
    assert claims.run_id == "123"


@pytest.mark.parametrize(
    ("field", "drifted"),
    [
        ("issuer", "https://issuer.example"),
        ("audience", "broader-audience"),
        ("subject", "repo:foreign/repository:environment:production-collector"),
        ("repository", "foreign/repository"),
        ("repository_id", "999"),
        ("repository_owner_id", "998"),
        ("workflow_ref", "foreign/repository/.github/workflows/other.yml@main"),
        ("git_ref", "refs/heads/release"),
        ("environment", "production"),
        ("run_id", "124"),
        ("run_attempt", "3"),
        ("head_sha", "f" * 40),
    ],
)
@pytest.mark.asyncio
async def test_activation_endpoint_rejects_each_authenticated_claim_drift(
    field: str,
    drifted: str,
) -> None:
    # Given: one authenticated GitHub claim drifting from the request/policy.
    verifier = _RecordingOidcVerifier(_claims().model_copy(update={field: drifted}))
    authorizer = ActivationEvidenceOidcAuthorizer(
        verifier=verifier,
        clock=_FixedClock(NOW),
        repository=REPOSITORY,
    )

    # When/Then: exact activation identity binding rejects the request.
    with pytest.raises(IdentityError):
        _ = await authorizer.authorize(SecretStr(RAW_OIDC_TOKEN), _payload())


@pytest.mark.parametrize(
    ("field", "drifted"),
    [
        ("issued_at", int(NOW.timestamp()) + TOKEN_SKEW_SECONDS + 1),
        ("not_before", int(NOW.timestamp()) + TOKEN_SKEW_SECONDS + 1),
        ("expires_at", int(NOW.timestamp()) - TOKEN_SKEW_SECONDS - 1),
    ],
)
@pytest.mark.asyncio
async def test_activation_endpoint_rejects_invalid_oidc_time_claims(
    field: str,
    drifted: int,
) -> None:
    # Given: one authenticated token time outside the accepted skew window.
    verifier = _RecordingOidcVerifier(_claims().model_copy(update={field: drifted}))
    authorizer = ActivationEvidenceOidcAuthorizer(
        verifier=verifier,
        clock=_FixedClock(NOW),
        repository=REPOSITORY,
    )

    # When/Then: future or stale authenticated workflow identity fails closed.
    with pytest.raises(IdentityError):
        _ = await authorizer.authorize(SecretStr(RAW_OIDC_TOKEN), _payload())


def test_attestation_url_normalization_has_one_canonical_hash() -> None:
    # Given: a bare HTTPS origin whose Pydantic representation gains a slash.
    attestation = _attestation()

    # When: canonical bytes are produced at the shared boundary.
    canonical = canonical_attestation_bytes(attestation)

    # Then: every caller hashes the same normalized URL representation.
    assert b'"public_evidence_urls":["https://example.com/"]' in canonical
    assert sha256(canonical).hexdigest() == sha256(
        canonical_attestation_bytes(
            PublicActivationAttestation.model_validate_json(canonical)
        )
    ).hexdigest()


def test_activation_receipt_field_order_has_one_canonical_hash() -> None:
    # Given: FastAPI's valid field-order response rather than sorted JSON keys.
    receipt = ActivationEvidenceReceipt.model_validate(
        {
            "schema_version": 1,
            "accepted": True,
            "activation_nonce": "11111111-1111-4111-8111-111111111111",
            "attestation_generation": 1,
            "attestation_sha256": "a" * 64,
            "reservation_receipt_sha256": "b" * 64,
            "dispatch_nonce": "22222222-2222-4222-8222-222222222222",
            "attempt": 1,
            "run_id": 123,
            "run_attempt": 1,
            "head_sha": "c" * 40,
            "database_time": "2026-08-03T05:48:33.899994Z",
        }
    )

    # When: the shared protected-boundary canonicalizer serializes it.
    canonical = canonical_evidence_receipt_bytes(receipt)

    # Then: keys are sorted and repeated canonicalization is byte-identical.
    assert canonical.startswith(b'{"accepted":true,"activation_nonce":')
    assert canonical == canonical_evidence_receipt_bytes(
        ActivationEvidenceReceipt.model_validate_json(canonical)
    )
