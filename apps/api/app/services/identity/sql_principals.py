"""Durable principal, credential-version, and worker-proof checks."""

from __future__ import annotations

from hashlib import sha256
from typing import TYPE_CHECKING, final
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.auth_models import PrincipalCredentialVersion, ServicePrincipal
from app.db.operations_models import CapabilityProofRecord
from app.domain.enums import (
    CapabilityKind,
    ProofStatus,
)
from app.domain.enums import (
    PrincipalKind as DomainPrincipalKind,
)

from .ports import PrincipalAuthorizationDecision

if TYPE_CHECKING:
    from app.db.session import DatabaseSessions

    from .ports import (
        GitHubPrincipalRegistration,
        PrincipalAuthorizationRequest,
        WorkerApprovalRequest,
    )


@final
class SqlPrincipalAuthorizationRepository:
    """Check principal revocation and credential validity in one transaction."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        """Bind principal reads to the production session owner."""
        self._sessions = sessions

    async def authorize(
        self, request: PrincipalAuthorizationRequest
    ) -> PrincipalAuthorizationDecision:
        """Return one atomic revocation and credential-version decision."""
        try:
            version = int(request.credential_version)
        except ValueError:
            return PrincipalAuthorizationDecision(authorized=False)
        async with self._sessions.open() as session, session.begin():
            statement = (
                select(ServicePrincipal.id)
                .join(
                    PrincipalCredentialVersion,
                    PrincipalCredentialVersion.principal_id == ServicePrincipal.id,
                )
                .where(
                    ServicePrincipal.subject == request.principal_id,
                    ServicePrincipal.active.is_(True),
                    ServicePrincipal.revoked_at.is_(None),
                    PrincipalCredentialVersion.version == version,
                    PrincipalCredentialVersion.active.is_(True),
                    PrincipalCredentialVersion.revoked_at.is_(None),
                    PrincipalCredentialVersion.valid_from <= request.checked_at,
                    PrincipalCredentialVersion.valid_until > request.checked_at,
                )
                .with_for_update(of=(ServicePrincipal, PrincipalCredentialVersion))
            )
            authorized = (await session.execute(statement)).scalar_one_or_none()
        return PrincipalAuthorizationDecision(authorized is not None)

    async def register(self, request: GitHubPrincipalRegistration) -> bool:
        """Persist one reviewed workflow-run principal without reviving revocation."""
        try:
            version = int(request.credential_version)
            kind = DomainPrincipalKind(request.kind.value)
        except ValueError:
            return False
        async with self._sessions.open() as session, session.begin():
            principal = (
                await session.execute(
                    select(ServicePrincipal)
                    .where(
                        ServicePrincipal.kind == kind,
                        ServicePrincipal.subject == request.principal_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if principal is None:
                principal = ServicePrincipal(
                    id=uuid4(),
                    kind=kind,
                    subject=request.principal_id,
                    active=True,
                    revoked_at=None,
                    created_at=request.valid_from,
                )
                session.add(principal)
            elif not principal.active or principal.revoked_at is not None:
                return False
            credential = (
                await session.execute(
                    select(PrincipalCredentialVersion)
                    .where(
                        PrincipalCredentialVersion.principal_id == principal.id,
                        PrincipalCredentialVersion.version == version,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if credential is None:
                verifier_hash = sha256(
                    f"github-oidc-principal/v1\n{request.principal_id}\n{request.workflow_ref}\n{request.credential_version}".encode()
                ).digest()
                session.add(
                    PrincipalCredentialVersion(
                        id=uuid4(),
                        principal_id=principal.id,
                        version=version,
                        verifier_hash=verifier_hash,
                        active=True,
                        valid_from=request.valid_from,
                        valid_until=request.valid_until,
                        revoked_at=None,
                        created_at=request.valid_from,
                    )
                )
                return True
            if not credential.active or credential.revoked_at is not None:
                return False
            credential.valid_until = max(credential.valid_until, request.valid_until)
            return credential.valid_from <= request.valid_from


@final
class SqlWorkerApprovalRepository:
    """Require an active worker principal, credential, and approved proof."""

    def __init__(self, sessions: DatabaseSessions) -> None:
        """Bind worker approval reads to the production session owner."""
        self._sessions = sessions

    async def authorize(self, request: WorkerApprovalRequest) -> bool:
        """Validate the exact active worker, version, and capability proof."""
        try:
            proof_id = UUID(request.capability_proof_id)
            version = int(request.credential_version)
        except ValueError:
            return False
        async with self._sessions.open() as session, session.begin():
            statement = (
                select(CapabilityProofRecord.id)
                .join(
                    ServicePrincipal,
                    CapabilityProofRecord.principal_id == ServicePrincipal.id,
                )
                .join(
                    PrincipalCredentialVersion,
                    PrincipalCredentialVersion.principal_id == ServicePrincipal.id,
                )
                .where(
                    CapabilityProofRecord.id == proof_id,
                    CapabilityProofRecord.kind == CapabilityKind.AUTOMATION_TERMS,
                    CapabilityProofRecord.status == ProofStatus.APPROVED,
                    CapabilityProofRecord.effective_at <= request.checked_at,
                    CapabilityProofRecord.expires_at > request.checked_at,
                    CapabilityProofRecord.revoked_at.is_(None),
                    ServicePrincipal.kind == DomainPrincipalKind.WINDOWS_WORKER,
                    ServicePrincipal.subject == f"worker:{request.worker_id}",
                    ServicePrincipal.active.is_(True),
                    ServicePrincipal.revoked_at.is_(None),
                    PrincipalCredentialVersion.version == version,
                    PrincipalCredentialVersion.active.is_(True),
                    PrincipalCredentialVersion.valid_from <= request.checked_at,
                    PrincipalCredentialVersion.valid_until > request.checked_at,
                    PrincipalCredentialVersion.revoked_at.is_(None),
                )
                .with_for_update(of=(ServicePrincipal, PrincipalCredentialVersion))
            )
            return (await session.execute(statement)).scalar_one_or_none() is not None


__all__ = (
    "SqlPrincipalAuthorizationRepository",
    "SqlWorkerApprovalRepository",
)
