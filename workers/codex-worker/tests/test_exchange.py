import hashlib
import hmac
from datetime import UTC, datetime

from pydantic import SecretBytes

from monitor_worker.credentials import CredentialTarget
from monitor_worker.exchange import (
    ExchangeClientDependencies,
    ExchangeSettings,
    ExchangeToken,
    HmacExchangeClient,
    SignedBootstrapRequest,
)

NOW = datetime(2026, 7, 22, tzinfo=UTC)
ACCESS_VALUE = "short-lived-value"


class FakeCredentials:
    def __init__(self, secret: bytes) -> None:
        self.secret: bytes = secret
        self.reads: int = 0

    def read_secret(self, target: CredentialTarget) -> SecretBytes:
        self.reads += 1
        assert target.value == "PredictionMarketMonitor/worker-bootstrap"
        return SecretBytes(self.secret)


class FixedNonce:
    def new(self) -> str:
        return "11111111-1111-4111-8111-111111111111"


class FixedClock:
    def now(self) -> datetime:
        return NOW


class RecordingTransport:
    def __init__(self) -> None:
        self.request: SignedBootstrapRequest | None = None

    def exchange(self, request: SignedBootstrapRequest) -> ExchangeToken:
        self.request = request
        return ExchangeToken(
            access_token=ACCESS_VALUE,
            expires_at=NOW,
            scope=("worker:ack", "worker:heartbeat", "worker:lease"),
        )


def test_exchange_signs_canonical_request_with_credential_manager_secret() -> None:
    # Given
    secret = b"s" * 32
    credentials = FakeCredentials(secret)
    transport = RecordingTransport()
    client = HmacExchangeClient(
        ExchangeClientDependencies(credentials, FixedNonce(), FixedClock(), transport)
    )
    settings = ExchangeSettings(
        worker_id="desktop-1",
        capability_proof_id="proof-set-1",
        credential_version="worker-v1",
    )

    # When
    token = client.exchange(settings)

    # Then
    assert credentials.reads == 1
    assert token.scope == ("worker:ack", "worker:heartbeat", "worker:lease")
    assert transport.request is not None
    expected = hmac.new(
        secret, transport.request.signing_payload(), hashlib.sha256
    ).hexdigest()
    assert transport.request.signature == expected
