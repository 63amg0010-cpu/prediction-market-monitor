"""Windows worker bootstrap HMAC authorization."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, ClassVar, final

from pydantic import BaseModel, ConfigDict, Field, SecretBytes

from app.core.errors import IdentityError, IdentityErrorCode
from app.core.jwt import TOKEN_SKEW_SECONDS
from app.core.principals import CredentialVersion, PrincipalId, Scope

from .ports import NonceRepository, WorkerApprovalRepository, WorkerApprovalRequest

if TYPE_CHECKING:
    from collections.abc import Mapping

WORKER_REQUEST_MAX_AGE_SECONDS = 120
WORKER_TOKEN_TTL_SECONDS = 600
WORKER_REFRESH_AFTER_SECONDS = 480


class WorkerBootstrapRequest(BaseModel):
    """Signed worker bootstrap fields with deterministic canonical bytes."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    worker_id: str = Field(min_length=1)
    capability_proof_id: str = Field(min_length=1)
    timestamp: datetime
    nonce: str = Field(min_length=1)
    credential_version: CredentialVersion
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")

    def signing_payload(self) -> bytes:
        """Return the versioned canonical payload authenticated by HMAC."""
        timestamp = int(self.timestamp.timestamp())
        return "\n".join(
            (
                "worker-bootstrap-v1",
                self.worker_id,
                self.capability_proof_id,
                str(timestamp),
                self.nonce,
                self.credential_version,
            )
        ).encode()


@dataclass(frozen=True, slots=True)
class VerifiedWorkerBootstrap:
    """HMAC-authenticated worker request awaiting durable approval checks."""

    principal_id: PrincipalId
    credential_version: CredentialVersion
    scopes: frozenset[Scope]


@final
class WorkerBootstrapVerifier:
    """Verify HMAC, age, and credential-version rotation allowlist."""

    def __init__(self, secrets: Mapping[CredentialVersion, SecretBytes]) -> None:
        """Configure the accepted bootstrap credential-version allowlist."""
        self._secrets = dict(secrets)

    def verify(
        self, request: WorkerBootstrapRequest, now: datetime
    ) -> VerifiedWorkerBootstrap:
        """Verify one canonical worker request without consuming state."""
        age = (now - request.timestamp).total_seconds()
        if age > WORKER_REQUEST_MAX_AGE_SECONDS or age < -TOKEN_SKEW_SECONDS:
            raise IdentityError(
                IdentityErrorCode.STALE_REQUEST, "worker bootstrap request is stale"
            )
        secret = self._secrets.get(request.credential_version)
        supplied = bytes.fromhex(request.signature)
        expected = hashlib.sha256(b"invalid-version").digest()
        if secret is not None:
            expected = hmac.new(
                secret.get_secret_value(), request.signing_payload(), hashlib.sha256
            ).digest()
        if secret is None or not hmac.compare_digest(expected, supplied):
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "worker bootstrap credential rejected",
            )
        return VerifiedWorkerBootstrap(
            principal_id=PrincipalId(f"worker:{request.worker_id}"),
            credential_version=request.credential_version,
            scopes=frozenset(
                {Scope.WORKER_LEASE, Scope.WORKER_HEARTBEAT, Scope.WORKER_ACK}
            ),
        )


@final
class WorkerExchangeAuthorizer:
    """Combine HMAC, approval, version, and one-use nonce checks."""

    def __init__(
        self,
        *,
        verifier: WorkerBootstrapVerifier,
        approvals: WorkerApprovalRepository,
        nonces: NonceRepository,
    ) -> None:
        """Configure HMAC, durable proof approval, and nonce repositories."""
        self._verifier = verifier
        self._approvals = approvals
        self._nonces = nonces

    async def authorize(
        self, request: WorkerBootstrapRequest, now: datetime
    ) -> VerifiedWorkerBootstrap:
        """Authorize an approved worker and consume its nonce once."""
        verified = self._verifier.verify(request, now)
        approved = await self._approvals.authorize(
            WorkerApprovalRequest(
                worker_id=request.worker_id,
                capability_proof_id=request.capability_proof_id,
                credential_version=request.credential_version,
                checked_at=now,
            )
        )
        if not approved:
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "worker capability proof is not approved",
            )
        consumed = await self._nonces.consume_once(
            "worker-bootstrap",
            request.nonce,
            now + timedelta(minutes=10),
        )
        if not consumed:
            raise IdentityError(
                IdentityErrorCode.REPLAYED_NONCE,
                "worker bootstrap nonce was already used",
            )
        return verified
