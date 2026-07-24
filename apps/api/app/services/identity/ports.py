"""Persistent identity repository ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from app.core.principals import CredentialVersion, PrincipalId, PrincipalKind


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """An exact fixed-window rate-limit contract."""

    bucket: str
    limit: int
    window_seconds: int


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Atomic result of consuming one rate-limit allowance."""

    allowed: bool
    retry_after_seconds: int


@dataclass(frozen=True, slots=True)
class PrincipalAuthorizationRequest:
    """Token state that must be checked atomically with principal revocation."""

    principal_id: PrincipalId
    credential_version: CredentialVersion
    jwt_id: str
    checked_at: datetime


@dataclass(frozen=True, slots=True)
class PrincipalAuthorizationDecision:
    """Atomic principal and credential-version authorization result."""

    authorized: bool


@dataclass(frozen=True, slots=True)
class GitHubPrincipalRegistration:
    """Reviewed workflow-run identity eligible for scoped token use."""

    principal_id: PrincipalId
    kind: PrincipalKind
    credential_version: CredentialVersion
    workflow_ref: str
    valid_from: datetime
    valid_until: datetime


@dataclass(frozen=True, slots=True)
class WorkerApprovalRequest:
    """Worker proof tuple checked against durable capability approval."""

    worker_id: str
    capability_proof_id: str
    credential_version: CredentialVersion
    checked_at: datetime


class NonceRepository(Protocol):
    """Atomically persist a nonce only when it has not previously been used."""

    async def consume_once(
        self, namespace: str, key: str, retain_until: datetime
    ) -> bool:
        """Atomically consume the key until the retention time."""
        ...


class RateLimitRepository(Protocol):
    """Atomically consume a fixed-window rate-limit allowance."""

    async def consume(
        self, key: str, rule: RateLimitRule, now: datetime
    ) -> RateLimitDecision:
        """Atomically consume one allowance in the named bucket."""
        ...


class PrincipalAuthorizationRepository(Protocol):
    """Atomically check principal revocation and credential version."""

    async def authorize(
        self, request: PrincipalAuthorizationRequest
    ) -> PrincipalAuthorizationDecision:
        """Check revocation and version in one repository operation."""
        ...


class GitHubPrincipalRepository(Protocol):
    """Register only policy-reviewed GitHub workflow-run identities."""

    async def register(self, request: GitHubPrincipalRegistration) -> bool:
        """Create or extend an active reviewed principal without reviving revocation."""
        ...


class WorkerApprovalRepository(Protocol):
    """Read a current worker capability proof without granting new authority."""

    async def authorize(self, request: WorkerApprovalRequest) -> bool:
        """Return whether the exact worker proof remains approved."""
        ...
