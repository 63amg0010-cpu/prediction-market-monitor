"""HMAC worker-token exchange client."""

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Literal, Protocol, final

from pydantic import BaseModel, ConfigDict, Field

from .credentials import CredentialManager, CredentialTarget


class SignedBootstrapRequest(BaseModel):
    """Wire-compatible canonical worker bootstrap request."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )

    worker_id: str = Field(min_length=1, max_length=100)
    capability_proof_id: str = Field(min_length=1, max_length=200)
    timestamp: datetime
    nonce: str = Field(min_length=1, max_length=100)
    credential_version: str = Field(min_length=1, max_length=100)
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")

    def signing_payload(self) -> bytes:
        """Return bytes matching the FastAPI worker-bootstrap-v1 verifier."""
        return "\n".join(
            (
                "worker-bootstrap-v1",
                self.worker_id,
                self.capability_proof_id,
                str(int(self.timestamp.timestamp())),
                self.nonce,
                self.credential_version,
            )
        ).encode()


class ExchangeToken(BaseModel):
    """Short-lived, exact-scope worker token response."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )

    access_token: str = Field(min_length=1)
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105
    expires_at: datetime
    scope: tuple[Literal["worker:ack", "worker:heartbeat", "worker:lease"], ...]


class NonceFactory(Protocol):
    """One-use nonce source."""

    def new(self) -> str:
        """Return a new unpredictable request nonce."""
        ...


class Clock(Protocol):
    """Clock used for the signed request timestamp."""

    def now(self) -> datetime:
        """Return current aware UTC time."""
        ...


class ExchangeTransport(Protocol):
    """HTTPS boundary for the signed exchange request."""

    def exchange(self, request: SignedBootstrapRequest) -> ExchangeToken:
        """Send the request and strictly parse the token response."""
        ...


@dataclass(frozen=True, slots=True)
class ExchangeClientDependencies:
    """Secret, nonce, time, and HTTPS ports for one exchange."""

    credentials: CredentialManager
    nonce_factory: NonceFactory
    clock: Clock
    transport: ExchangeTransport


@dataclass(frozen=True, slots=True)
class ExchangeSettings:
    """Version-bound worker identity sent in the signed request."""

    worker_id: str
    capability_proof_id: str
    credential_version: str


@final
class HmacExchangeClient:
    """Exchange a Credential Manager secret for a ten-minute worker token."""

    def __init__(self, dependencies: ExchangeClientDependencies) -> None:
        """Configure secret, nonce, clock, and HTTPS ports."""
        self._dependencies = dependencies

    def exchange(self, settings: ExchangeSettings) -> ExchangeToken:
        """Sign one request and keep the bootstrap secret out of its body."""
        unsigned = SignedBootstrapRequest(
            worker_id=settings.worker_id,
            capability_proof_id=settings.capability_proof_id,
            timestamp=self._dependencies.clock.now(),
            nonce=self._dependencies.nonce_factory.new(),
            credential_version=settings.credential_version,
            signature="0" * 64,
        )
        secret = self._dependencies.credentials.read_secret(
            CredentialTarget.WORKER_BOOTSTRAP
        )
        signature = hmac.new(
            secret.get_secret_value(), unsigned.signing_payload(), hashlib.sha256
        ).hexdigest()
        return self._dependencies.transport.exchange(
            unsigned.model_copy(update={"signature": signature})
        )
