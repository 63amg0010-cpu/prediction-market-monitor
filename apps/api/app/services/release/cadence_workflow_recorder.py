"""Atomic PostgreSQL cadence workflow attempt recorder."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast, final
from uuid import NAMESPACE_URL, UUID, uuid5

from app.core.errors import IdentityError, IdentityErrorCode
from app.services.release.cadence_workflow_receipts import (
    build_receipt,
    same_attempt,
)
from app.services.release.cadence_workflow_sql import (
    CAS,
    EXISTING,
    EXISTING_SOURCES,
    FINALIZE,
    INSERT_ATTEMPT,
    INSERT_SOURCE,
    LOAD,
    LOCK,
)
from app.services.release.cadence_workflow_validation import (
    rejection_reason,
    require_workflow_identity,
    retry_permitted,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import SecretStr
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.session import DatabaseSessions
    from app.services.release.cadence_workflow_models import (
        CadenceOidcAuthorizer,
        CadenceWorkflowAttemptReceipt,
        CadenceWorkflowAttemptRequest,
    )


@final
class SqlCadenceWorkflowRecorder:
    """Verify OIDC and atomically retain one exact workflow attempt."""

    def __init__(
        self,
        sessions: DatabaseSessions,
        oidc: CadenceOidcAuthorizer,
    ) -> None:
        """Bind database sessions and the exact OIDC verifier."""
        self._sessions = sessions
        self._oidc = oidc

    async def record(
        self, token: SecretStr, payload: CadenceWorkflowAttemptRequest
    ) -> CadenceWorkflowAttemptReceipt:
        """Record idempotently or retain a fail-closed noncredit attempt."""
        _ = await self._oidc.authorize(token, payload)
        require_workflow_identity(payload)
        attempt_id = _attempt_id(payload)
        async with self._sessions.open() as session, session.begin():
            _ = await session.execute(LOCK, {"epoch_id": payload.epoch_id})
            existing = await _load_existing(session, attempt_id)
            if existing is not None:
                return await _replay(session, payload, attempt_id, existing)
            row = (
                await session.execute(
                    LOAD,
                    {
                        "epoch_id": payload.epoch_id,
                        "schedule_kind": payload.schedule_kind,
                        "slot_key": payload.slot_key,
                    },
                )
            ).mappings().one_or_none()
            if row is None:
                code = "cadence_slot_not_found"
                raise _reject(code)
            slot = cast("Mapping[str, object]", row)
            db_now = slot["db_now"]
            if not isinstance(db_now, datetime):
                code = "cadence_database_time_invalid"
                raise _reject(code)
            reason = await rejection_reason(session, payload, slot, db_now)
            retry = retry_permitted(payload, slot, reason)
            await _insert(
                session, payload, attempt_id, slot, (reason, retry)
            )
            accepted = await _accept(
                session, payload, attempt_id, reason is None
            )
            if reason is None:
                reason = (
                    "accepted" if accepted else "duplicate_after_acceptance"
                )
            final_retry = retry if not accepted else False
            _ = await session.execute(
                FINALIZE,
                {
                    "attempt_id": attempt_id,
                    "accepted": accepted,
                    "reason_code": reason,
                    "retry_permitted": final_retry,
                },
            )
        return build_receipt(
            attempt_id, accepted, reason, final_retry, db_now
        )


async def _insert(
    session: AsyncSession,
    payload: CadenceWorkflowAttemptRequest,
    attempt_id: UUID,
    slot: Mapping[str, object],
    decision: tuple[str | None, bool],
) -> None:
    reason, retry = decision
    parameters = {
        "attempt_id": attempt_id,
        "epoch_id": payload.epoch_id,
        "schedule_kind": payload.schedule_kind,
        "slot_key": payload.slot_key,
        "workflow_mode": payload.workflow_mode,
        "workflow_file": payload.workflow,
        "workflow_run_id": payload.run_id,
        "workflow_run_attempt": payload.run_attempt,
        "cadence_attempt": payload.cadence_attempt,
        "failed_predecessor_attempt_id": payload.failed_predecessor_attempt_id,
        "started_at": payload.started_at,
        "completed_at": payload.completed_at,
        "eligible": reason is None,
        "epoch_sha256": slot["epoch_sha256"],
        "binding_sha256": slot["binding_sha256"],
        "scope_sha256": slot["scope_sha256"],
        "reason_code": reason or "eligible",
        "retry_permitted": retry,
    }
    inserted = await session.execute(INSERT_ATTEMPT, parameters)
    if cast("CursorResult[object]", inserted).rowcount != 1:
        code = "cadence_attempt_insert_conflict"
        raise _reject(code)
    for source in payload.source_results:
        _ = await session.execute(
            INSERT_SOURCE,
            {
                "attempt_id": attempt_id,
                "source_id": source.source_id,
                "succeeded": source.status == "succeeded",
                "receipt_sha256": source.receipt_sha256,
            },
        )


async def _accept(
    session: AsyncSession,
    payload: CadenceWorkflowAttemptRequest,
    attempt_id: UUID,
    eligible: bool,
) -> bool:
    if not eligible:
        return False
    result = await session.execute(
        CAS,
        {
            "attempt_id": attempt_id,
            "epoch_id": payload.epoch_id,
            "schedule_kind": payload.schedule_kind,
            "slot_key": payload.slot_key,
        },
    )
    return cast("CursorResult[object]", result).rowcount == 1


async def _load_existing(
    session: AsyncSession, attempt_id: UUID
) -> Mapping[str, object] | None:
    row = (
        await session.execute(EXISTING, {"attempt_id": attempt_id})
    ).mappings().one_or_none()
    return None if row is None else cast("Mapping[str, object]", row)


async def _replay(
    session: AsyncSession,
    payload: CadenceWorkflowAttemptRequest,
    attempt_id: UUID,
    existing: Mapping[str, object],
) -> CadenceWorkflowAttemptReceipt:
    rows = (
        await session.execute(EXISTING_SOURCES, {"attempt_id": attempt_id})
    ).mappings()
    sources = tuple(cast("Mapping[str, object]", item) for item in rows)
    if not same_attempt(payload, existing, sources):
        code = "cadence_attempt_replay_conflict"
        raise _reject(code)
    created_at = existing["created_at_db"]
    if not isinstance(created_at, datetime):
        code = "cadence_attempt_created_at_invalid"
        raise _reject(code)
    return build_receipt(
        attempt_id,
        bool(existing["accepted"]),
        str(existing["reason_code"]),
        bool(existing["retry_permitted"]),
        created_at,
    )


def _attempt_id(payload: CadenceWorkflowAttemptRequest) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        (
            f"cadence|{payload.epoch_id}|{payload.schedule_kind}|"
            f"{payload.slot_key}|{payload.workflow}|{payload.run_id}|"
            f"{payload.run_attempt}"
        ),
    )


def _reject(code: str) -> IdentityError:
    return IdentityError(IdentityErrorCode.INVALID_CREDENTIAL, code)
