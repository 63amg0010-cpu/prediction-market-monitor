"""Fail-closed cadence branch, source, and timing validation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from app.core.errors import IdentityError, IdentityErrorCode
from app.services.release.cadence_workflow_models import (
    SECOND_ATTEMPT,
    CadenceWorkflowAttemptRequest,
)
from app.services.release.cadence_workflow_sql import (
    LOAD_FAILED,
    LOAD_LOGICAL_ATTEMPT,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession


async def rejection_reason(
    session: AsyncSession,
    payload: CadenceWorkflowAttemptRequest,
    row: Mapping[str, object],
    db_now: datetime,
) -> str | None:
    """Return the first stable noncredit reason, if any."""
    basic = _basic_reason(payload, row)
    if basic is not None:
        return basic
    if row["accepted_attempt_id"] is not None:
        return "duplicate_after_acceptance"
    branch = await branch_rejection(session, payload)
    return branch if branch is not None else _time_reason(payload, row, db_now)


def retry_permitted(
    payload: CadenceWorkflowAttemptRequest,
    row: Mapping[str, object],
    reason: str | None,
) -> bool:
    """Permit only one still-timely explicit retry of a failed first attempt."""
    safe_failure = any(
        item.status == "failed" for item in payload.source_results
    ) and all(
        item.status == "succeeded"
        or item.retry_classification == "safe_terminal"
        for item in payload.source_results
    )
    if (
        reason != "source_failed"
        or payload.workflow_mode != "schedule"
        or payload.cadence_attempt != 1
        or not safe_failure
    ):
        return False
    due = row["due_at"]
    if not isinstance(due, datetime):
        return False
    limit = timedelta(minutes=30 if payload.schedule_kind == "collection" else 5)
    return payload.completed_at.astimezone(UTC) < due + limit


def require_workflow_identity(payload: CadenceWorkflowAttemptRequest) -> None:
    """Bind workflow file, kind, and protected environment exactly."""
    expected = (
        ("collect.yml", "collection", "production-collector"),
        ("verify.yml", "verifier", "production-verifier"),
    )
    if (payload.workflow, payload.schedule_kind, payload.environment) not in expected:
        code = "cadence_workflow_identity_invalid"
        raise IdentityError(IdentityErrorCode.INVALID_CREDENTIAL, code)


async def branch_rejection(
    session: AsyncSession,
    payload: CadenceWorkflowAttemptRequest,
) -> str | None:
    """Validate the unique initial/retry branch and exact predecessor."""
    if payload.workflow_mode == "manual":
        return None
    if payload.workflow_mode == "schedule":
        valid = (
            payload.event == "schedule"
            and payload.cadence_attempt == 1
            and payload.failed_predecessor_attempt_id is None
        )
        if not valid:
            return "initial_attempt_identity_invalid"
        return await _logical_duplicate(session, payload)
    valid_retry = (
        payload.event == "workflow_dispatch"
        and payload.cadence_attempt == SECOND_ATTEMPT
        and payload.failed_predecessor_attempt_id is not None
    )
    if not valid_retry:
        return "retry_proof_required"
    failed = (
        await session.execute(
            LOAD_FAILED,
            {"attempt_id": payload.failed_predecessor_attempt_id},
        )
    ).mappings().one_or_none()
    failed_row = None if failed is None else cast("Mapping[str, object]", failed)
    expected = (payload.epoch_id, payload.schedule_kind, payload.slot_key)
    actual = (
        None
        if failed_row is None
        else (
            failed_row["cadence_epoch_id"],
            failed_row["schedule_kind"],
            failed_row["slot_key"],
        )
    )
    if (
        failed_row is None
        or bool(failed_row["accepted"])
        or not bool(failed_row["retry_permitted"])
        or actual != expected
    ):
        return "retry_proof_invalid"
    return await _logical_duplicate(session, payload)


async def _logical_duplicate(
    session: AsyncSession,
    payload: CadenceWorkflowAttemptRequest,
) -> str | None:
    row = (
        await session.execute(
            LOAD_LOGICAL_ATTEMPT,
            {
                "epoch_id": payload.epoch_id,
                "schedule_kind": payload.schedule_kind,
                "slot_key": payload.slot_key,
                "cadence_attempt": payload.cadence_attempt,
            },
        )
    ).one_or_none()
    if row is None:
        return None
    if payload.cadence_attempt == 1:
        return "initial_attempt_already_recorded"
    return "retry_attempt_already_recorded"


def _basic_reason(
    payload: CadenceWorkflowAttemptRequest,
    row: Mapping[str, object],
) -> str | None:
    sources = {item.source_id for item in payload.source_results}
    expected = {row["dcinside_source_id"], row["manifold_source_id"]}
    if row["invalidated_at"] is not None:
        return "epoch_invalidated"
    if sources != expected:
        return "source_set_mismatch"
    if not all(item.status == "succeeded" for item in payload.source_results):
        return "source_failed"
    if payload.workflow_mode == "manual":
        return "manual_mode_excluded"
    return None


def _time_reason(
    payload: CadenceWorkflowAttemptRequest,
    row: Mapping[str, object],
    db_now: datetime,
) -> str | None:
    due = row["due_at"]
    if not isinstance(due, datetime):
        return "slot_time_invalid"
    started = payload.started_at.astimezone(UTC)
    completed = payload.completed_at.astimezone(UTC)
    start_limit = timedelta(minutes=30 if payload.schedule_kind == "collection" else 5)
    completion_limit = timedelta(
        minutes=36 if payload.schedule_kind == "collection" else 8
    )
    if started < due:
        return "started_early"
    if started >= due + start_limit:
        return "started_late"
    if completed < started:
        return "completed_before_start"
    if completed >= started + completion_limit or completed > db_now:
        return "completed_late"
    return None
