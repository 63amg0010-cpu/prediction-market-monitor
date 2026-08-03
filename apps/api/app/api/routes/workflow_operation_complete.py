"""OIDC-authenticated terminal receipts for reserved release workflows."""

from __future__ import annotations

import hmac
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, cast, final

from fastapi import APIRouter, Header, Response
from pydantic import Field
from sqlalchemy import text

from app.core.errors import IdentityError, IdentityErrorCode
from app.services.dashboard.security import bearer_token
from app.services.release.receipts import (
    ClosedReceiptModel,
    Sha,
    Sha256,
    canonicalize,
)
from app.services.release.workflow_claims import WorkflowDispatchClaimRequest

if TYPE_CHECKING:
    from pydantic import SecretStr

    from app.api.routes.workflow_dispatch_claim import (
        WorkflowDispatchClaimOidcAuthorizer,
    )
    from app.db.session import DatabaseSessions

_ALLOWED_COMMANDS = {
    "activation-evidence.yml": frozenset({"activation-evidence"}),
    "ci.yml": frozenset({"ci"}),
    "collect.yml": frozenset(
        {
            "binding-handshake",
            "binding-prestate",
            "binding-restore-verify",
            "smoke-collection",
        }
    ),
    "migrate.yml": frozenset({"migrate-0011", "migrate-0011-to-0010g"}),
    "verify.yml": frozenset({"smoke-verifier"}),
}
_APPROVAL_LAUNCH_COUNT = 2

RESERVATION_SQL = """
SELECT reservation.receipt_sha256, reservation.reviewed_sha,
       reservation.approved_plan_sha256, reservation.approval_round_id,
       reservation.approval_launch_sha256s, reservation.activation_nonce,
       reservation.dispatch_nonce, reservation.attempt,
       reservation.claimed_run_id, reservation.claimed_run_attempt,
       (SELECT version_num FROM alembic_version) AS current_revision
FROM release_operation_reservations AS reservation
WHERE reservation.receipt_sha256 = :reservation_sha256
  AND reservation.repository = :repository
  AND reservation.git_ref = :ref
  AND reservation.workflow_file = :workflow
  AND reservation.display_title = :display_title
  AND reservation.head_sha = :head_sha
  AND reservation.approved_plan_sha256 = :approved_plan_sha256
  AND reservation.activation_nonce = :activation_nonce
  AND reservation.dispatch_nonce = :dispatch_nonce
  AND reservation.claimed_run_id = :run_id
  AND reservation.claimed_run_attempt = :run_attempt
FOR SHARE
"""

INSERT_OPERATION_SQL = """
INSERT INTO release_operation_receipts (
    receipt_sha256, canonical_receipt, reservation_receipt_sha256,
    predecessor_receipt_sha256, reviewed_sha, approved_plan_sha256,
    activation_nonce, dispatch_nonce, operation, revision, attempt,
    run_id, head_sha, artifact_sha256, accepted, terminal_for_attempt,
    retry_permitted, state_before, state_after, enum_residue,
    committed_revision, created_at_db
) VALUES (
    :receipt_sha256, :canonical_receipt, :reservation_sha256,
    :reservation_sha256, :reviewed_sha, :approved_plan_sha256,
    :activation_nonce, :dispatch_nonce, NULL, NULL, :attempt,
    :run_id, :head_sha, :evidence_sha256, true, true, false,
    :current_revision, :current_revision, false, :current_revision,
    transaction_timestamp()
)
"""

INSERT_CHAIN_SQL = """
INSERT INTO release_receipt_chain (
    receipt_sha256, canonical_receipt, command, reviewed_sha,
    approved_plan_sha256, approval_round_id, approval_launch_sha256s,
    activation_nonce, dispatch_nonce, attempt, accepted,
    terminal_for_attempt, retry_permitted, predecessor_receipt_sha256,
    created_at_db
) VALUES (
    :receipt_sha256, :canonical_receipt, :command, :reviewed_sha,
    :approved_plan_sha256, :approval_round_id,
    CAST(:approval_launch_sha256s AS jsonb), :activation_nonce,
    :dispatch_nonce, :attempt, true, true, false,
    :reservation_sha256, transaction_timestamp()
)
"""

SELECT_EXISTING_SQL = """
SELECT canonical_receipt
FROM release_operation_receipts
WHERE reservation_receipt_sha256=:reservation_sha256
"""


class WorkflowOperationCompleteRequest(WorkflowDispatchClaimRequest):
    """Claim-bound terminal success input with one public evidence digest."""

    command: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    evidence_sha256: Sha256
    outcome: Literal["success"]


class WorkflowOperationCompleteResponse(ClosedReceiptModel):
    """Schema-closed terminal receipt consumed by release verification."""

    schema_version: Literal[1] = 1
    command: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    reviewed_sha: Sha
    approved_plan_sha256: Sha256
    approval_round_id: Sha256
    approval_launch_sha256s: tuple[Sha256, Sha256]
    activation_nonce: str
    dispatch_nonce: str
    attempt: int = Field(gt=0)
    accepted: Literal[True] = True
    terminal_for_attempt: Literal[True] = True
    retry_permitted: Literal[False] = False
    predecessor_receipt_sha256: Sha256
    reservation_receipt_sha256: Sha256
    run_id: int = Field(gt=0)
    head_sha: Sha
    artifact_sha256: Sha256
    state_before: str = Field(min_length=1, max_length=32)
    state_after: str = Field(min_length=1, max_length=32)
    enum_residue: Literal[False] = False
    committed_revision: str = Field(min_length=1, max_length=32)


class WorkflowOperationCompleter(Protocol):
    """Port for atomically appending one successful workflow outcome."""

    async def complete(
        self,
        token: SecretStr,
        payload: WorkflowOperationCompleteRequest,
    ) -> WorkflowOperationCompleteResponse:
        """Append and return one canonical public operation receipt."""
        ...


@final
class SqlWorkflowOperationCompleter:
    """Authorize the run and append its terminal operation exactly once."""

    def __init__(
        self,
        sessions: DatabaseSessions,
        oidc: WorkflowDispatchClaimOidcAuthorizer,
    ) -> None:
        """Bind database sessions and the existing exact-run OIDC policy."""
        self._sessions = sessions
        self._oidc = oidc

    async def complete(
        self,
        token: SecretStr,
        payload: WorkflowOperationCompleteRequest,
    ) -> WorkflowOperationCompleteResponse:
        """Persist a success only after OIDC and reservation bindings match."""
        if payload.command not in _ALLOWED_COMMANDS.get(payload.workflow, frozenset()):
            raise IdentityError(
                IdentityErrorCode.INVALID_CREDENTIAL,
                "workflow operation command rejected",
            )
        _ = await self._oidc.authorize(token, payload)
        parameters = {
            **payload.model_dump(mode="python"),
            "reservation_sha256": payload.reservation_sha256,
        }
        async with self._sessions.open() as session, session.begin():
            raw = (
                (await session.execute(text(RESERVATION_SQL), parameters))
                .mappings()
                .one_or_none()
            )
            if raw is None:
                raise IdentityError(
                    IdentityErrorCode.INVALID_CREDENTIAL,
                    "workflow operation reservation rejected",
                )
            row = cast("dict[str, object]", dict(raw))
            receipt = _operation_receipt(payload, row)
            canonical = canonicalize(receipt.model_dump(mode="json"))
            receipt_sha256 = sha256(canonical).hexdigest()
            existing_result = await session.execute(
                text(SELECT_EXISTING_SQL),
                parameters,
            )
            stored = cast("object", existing_result.scalar_one_or_none())
            if stored is not None:
                if not isinstance(
                    stored, (bytes, bytearray)
                ) or not hmac.compare_digest(bytes(stored), canonical):
                    raise IdentityError(
                        IdentityErrorCode.INVALID_CREDENTIAL,
                        "workflow operation already finalized",
                    )
                return receipt
            values = {
                **parameters,
                **row,
                "approval_launch_sha256s": canonicalize(
                    row["approval_launch_sha256s"]
                ).decode(),
                "canonical_receipt": canonical,
                "receipt_sha256": receipt_sha256,
            }
            _ = await session.execute(text(INSERT_OPERATION_SQL), values)
            _ = await session.execute(text(INSERT_CHAIN_SQL), values)
        return receipt


def _operation_receipt(
    payload: WorkflowOperationCompleteRequest,
    row: dict[str, object],
) -> WorkflowOperationCompleteResponse:
    current_revision = str(row["current_revision"])
    launches_raw = row["approval_launch_sha256s"]
    attempt = row["attempt"]
    if not isinstance(launches_raw, list) or not isinstance(attempt, int):
        error_code = "workflow_operation_row_invalid"
        raise TypeError(error_code)
    launches = cast("list[object]", launches_raw)
    if len(launches) != _APPROVAL_LAUNCH_COUNT:
        error_code = "workflow_operation_approval_chain_invalid"
        raise TypeError(error_code)
    launch_pair = (str(launches[0]), str(launches[1]))
    return WorkflowOperationCompleteResponse(
        activation_nonce=str(row["activation_nonce"]),
        approved_plan_sha256=str(row["approved_plan_sha256"]),
        approval_launch_sha256s=launch_pair,
        approval_round_id=str(row["approval_round_id"]),
        artifact_sha256=payload.evidence_sha256,
        attempt=attempt,
        command=payload.command,
        committed_revision=current_revision,
        dispatch_nonce=str(row["dispatch_nonce"]),
        head_sha=payload.head_sha,
        predecessor_receipt_sha256=payload.reservation_sha256,
        reservation_receipt_sha256=payload.reservation_sha256,
        reviewed_sha=str(row["reviewed_sha"]),
        run_id=payload.run_id,
        state_after=current_revision,
        state_before=current_revision,
    )


@final
class UnavailableWorkflowOperationCompleter:
    """Fail closed when production completion dependencies are absent."""

    async def complete(
        self,
        token: SecretStr,
        payload: WorkflowOperationCompleteRequest,
    ) -> WorkflowOperationCompleteResponse:
        """Reject without mutating state when dependencies are unavailable."""
        del token, payload
        raise IdentityError(
            IdentityErrorCode.SERVICE_UNAVAILABLE,
            "workflow operation completer is unavailable",
        )


def create_workflow_operation_complete_router(
    completer: WorkflowOperationCompleter,
) -> APIRouter:
    """Expose the least-privilege OIDC completion surface."""
    router = APIRouter(prefix="/internal/release", tags=["release"])

    @router.post("/workflow-operation-complete")
    async def complete(
        payload: WorkflowOperationCompleteRequest,
        response: Response,
        authorization: Annotated[str | None, Header()] = None,
    ) -> WorkflowOperationCompleteResponse:
        receipt = await completer.complete(bearer_token(authorization), payload)
        response.headers["Cache-Control"] = "no-store"
        return receipt

    _ = complete
    return router


__all__ = (
    "SqlWorkflowOperationCompleter",
    "UnavailableWorkflowOperationCompleter",
    "WorkflowOperationCompleteRequest",
    "WorkflowOperationCompleteResponse",
    "WorkflowOperationCompleter",
    "create_workflow_operation_complete_router",
)
