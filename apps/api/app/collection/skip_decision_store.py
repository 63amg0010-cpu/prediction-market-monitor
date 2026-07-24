"""Atomic server derivation of zero-commit policy and quota skip proofs."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, Select, func, select

from app.api.routes.collector_models import SkipDecisionResponse
from app.db.operations_models import CollectionSkipObservation
from app.db.page_models import PageCommit
from app.db.run_models import CollectionRun
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import RunStatus

from .base import (
    EXECUTION_STALE_SECONDS,
    CollectionError,
    CollectionErrorCode,
    canonical_json_hash,
    hash_token,
    token_matches,
)
from .skip_decision_models import SkipDecisionOperation, SkipDecisionOutcome
from .skip_decision_proofs import (
    SkipProofContext,
    attach_policy_proof,
    attach_quota_proof,
    current_authorization,
    run_authorization,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def attach_skip_decision(
    session: AsyncSession,
    operation: SkipDecisionOperation,
) -> SkipDecisionOutcome:
    """Attach exactly one proof after all ownership and evidence locks pass."""
    run = await _locked_run(session, operation)
    command = await _locked_command(session, operation)
    now = await _database_now(session)
    _require_bound_run(run, command, operation)
    request_hash = _request_hash(operation)
    existing = await _existing(session, run.id, operation.payload.idempotency_key)
    if existing is not None:
        if existing.request_hash != request_hash:
            raise CollectionError(CollectionErrorCode.IDEMPOTENCY_KEY_REUSED, 409)
        response = SkipDecisionResponse.model_validate_json(existing.stored_response)
        return SkipDecisionOutcome(200, response, existing.stored_response)
    _require_live_zero_commit(run, command, operation, now)
    if await _has_commits(session, run.id):
        raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)
    source, authorization = await current_authorization(session, run, now)
    snapshot = run_authorization(run)
    if (
        source.platform is not operation.payload.provider
        or operation.payload.route not in snapshot.permitted_routes
    ):
        raise CollectionError(CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403)
    observation_id = uuid4()
    evidence_sha256 = canonical_json_hash(
        {
            "attempt": operation.payload.attempt,
            "command_id": str(operation.payload.command_id),
            "failure_code": operation.payload.failure_code,
            "http_status": operation.payload.http_status,
            "provider": operation.payload.provider.value,
            "route": operation.payload.route,
            "run_id": str(run.id),
        }
    )
    evidence_location = f"urn:monitor:collection-skip:{observation_id}"
    proof_context = SkipProofContext(
        session, source, run, now, evidence_sha256, evidence_location
    )
    if operation.payload.http_status in (401, 403):
        decision_id = attach_policy_proof(proof_context, authorization)
        terminal_status = RunStatus.SKIPPED_POLICY
        decision_kind = "policy"
    else:
        decision_id = await attach_quota_proof(proof_context)
        terminal_status = RunStatus.SKIPPED_QUOTA
        decision_kind = "quota"
    response = SkipDecisionResponse(
        skip_decision_id=decision_id,
        terminal_status=terminal_status,
        evidence_sha256=evidence_sha256,
    )
    response_bytes = response.model_dump_json().encode()
    session.add(
        CollectionSkipObservation(
            id=observation_id,
            run_id=run.id,
            command_id=command.id,
            attempt=run.attempt,
            idempotency_key=operation.payload.idempotency_key,
            request_hash=request_hash,
            actor_principal_id=operation.actor_principal_id,
            provider=operation.payload.provider,
            route=operation.payload.route,
            http_status=operation.payload.http_status,
            failure_code=operation.payload.failure_code,
            decision_kind=decision_kind,
            decision_id=decision_id,
            evidence_sha256=evidence_sha256,
            evidence_location=evidence_location,
            stored_response=response_bytes,
            created_at=now,
        )
    )
    return SkipDecisionOutcome(201, response, response_bytes)


def _require_bound_run(
    run: CollectionRun,
    command: CollectionCommand,
    operation: SkipDecisionOperation,
) -> None:
    payload = operation.payload
    bound = (
        run.id == operation.run_id
        and run.command_id == payload.command_id == command.id
        and run.attempt == payload.attempt
        and hash_token(payload.lease_token) == run.lease_identity_hash
    )
    if not bound:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)


def _require_live_zero_commit(
    run: CollectionRun,
    command: CollectionCommand,
    operation: SkipDecisionOperation,
    now: datetime,
) -> None:
    payload = operation.payload
    heartbeat = run.heartbeat_at
    fresh = heartbeat is not None and now - heartbeat <= timedelta(
        seconds=EXECUTION_STALE_SECONDS
    )
    active = (
        command.attempt == payload.attempt
        and run.status is RunStatus.RUNNING
        and command.status.value == "running"
        and token_matches(payload.lease_token, command.lease_hash)
        and hash_token(payload.lease_token) == run.lease_identity_hash
        and fresh
    )
    empty = (
        run.committed_page_count == 0
        and run.last_page_commit_id is None
        and run.terminal_page_commit_id is None
        and run.page_reservation_id is None
        and run.skip_authorization_decision_id is None
        and run.skip_budget_decision_id is None
    )
    if not active:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    if not empty:
        raise CollectionError(CollectionErrorCode.COMPLETION_MISMATCH, 409)


def skip_run_lock_statement(
    operation: SkipDecisionOperation,
) -> Select[tuple[CollectionRun]]:
    """Lock only the path-bound run, command identity, and attempt."""
    return (
        select(CollectionRun)
        .where(
            CollectionRun.id == operation.run_id,
            CollectionRun.command_id == operation.payload.command_id,
            CollectionRun.attempt == operation.payload.attempt,
        )
        .with_for_update(of=CollectionRun)
    )


def skip_command_lock_statement(
    operation: SkipDecisionOperation,
) -> Select[tuple[CollectionCommand]]:
    """Lock only the command and attempt named by the bound run request."""
    return (
        select(CollectionCommand)
        .where(
            CollectionCommand.id == operation.payload.command_id,
            CollectionCommand.attempt == operation.payload.attempt,
        )
        .with_for_update(of=CollectionCommand)
    )


def skip_page_commits_lock_statement(
    run_id: UUID,
) -> Select[tuple[PageCommit]]:
    """Lock any first committed page before proving zero-commit state."""
    return (
        select(PageCommit)
        .where(PageCommit.run_id == run_id)
        .limit(1)
        .with_for_update(of=PageCommit)
    )


async def _locked_run(
    session: AsyncSession, operation: SkipDecisionOperation
) -> CollectionRun:
    row = (
        await session.execute(skip_run_lock_statement(operation))
    ).scalar_one_or_none()
    if row is None:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    return row


async def _locked_command(
    session: AsyncSession, operation: SkipDecisionOperation
) -> CollectionCommand:
    return (await session.execute(skip_command_lock_statement(operation))).scalar_one()


async def _existing(
    session: AsyncSession, run_id: UUID, key: UUID
) -> CollectionSkipObservation | None:
    return (
        await session.execute(
            select(CollectionSkipObservation)
            .where(
                CollectionSkipObservation.run_id == run_id,
                CollectionSkipObservation.idempotency_key == key,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()


async def _has_commits(session: AsyncSession, run_id: UUID) -> bool:
    return (
        await session.execute(skip_page_commits_lock_statement(run_id))
    ).scalar_one_or_none() is not None


def _request_hash(operation: SkipDecisionOperation) -> str:
    payload = operation.payload.model_dump(mode="json", exclude={"lease_token"})
    return canonical_json_hash(payload)


async def _database_now(session: AsyncSession) -> datetime:
    clock = func.clock_timestamp(type_=DateTime(timezone=True))
    return (await session.execute(select(clock))).scalar_one()


__all__ = (
    "attach_skip_decision",
    "skip_command_lock_statement",
    "skip_page_commits_lock_statement",
    "skip_run_lock_statement",
)
