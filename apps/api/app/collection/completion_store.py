"""Atomic completion receipts and source publication persistence."""

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.scheduler_models import CollectionCommand, CommandCompletion
from app.services.configuration.canonical import canonical_bytes

from .base import (
    CollectionError,
    CollectionErrorCode,
    token_matches,
)
from .completion import prepare_completion
from .completion_context_store import load_locked_completion_context
from .completion_models import (
    CompletionOutcome,
    CompletionRequest,
    CompletionResponse,
    completion_request_hash,
)
from .completion_plan_store import persist_completion_plan


@dataclass(frozen=True, slots=True)
class CompletionOperation:
    """Path-bound command identity paired with its parsed completion body."""

    command_id: UUID
    request: CompletionRequest


@dataclass(frozen=True, slots=True)
class CompletionServiceConfig:
    """Server-only deterministic retry jitter material."""

    retry_jitter_key: bytes


async def execute_completion(
    session: AsyncSession,
    operation: CompletionOperation,
    config: CompletionServiceConfig,
) -> CompletionOutcome:
    """Finalize every run and publication in the caller-owned transaction."""
    command_row = (
        await session.execute(
            select(CollectionCommand)
            .where(CollectionCommand.id == operation.command_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    request = operation.request
    if (
        command_row is None
        or command_row.attempt != request.attempt
        or not token_matches(request.lease_token, command_row.lease_hash)
    ):
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    request_hash = completion_request_hash(request)
    receipt = (
        await session.execute(
            select(CommandCompletion).where(
                CommandCompletion.command_id == operation.command_id,
                CommandCompletion.completion_idempotency_key
                == request.completion_idempotency_key,
            )
        )
    ).scalar_one_or_none()
    if receipt is not None:
        if receipt.request_hash != request_hash:
            raise CollectionError(
                CollectionErrorCode.COMPLETION_IDEMPOTENCY_MISMATCH,
                409,
            )
        response = CompletionResponse.model_validate_json(receipt.response_payload)
        return CompletionOutcome(200, response, receipt.response_payload)
    locked = await load_locked_completion_context(
        session,
        operation.command_id,
        request.attempt,
    )
    plan = prepare_completion(locked.domain, request)
    publications = await persist_completion_plan(
        session,
        locked,
        plan,
        config.retry_jitter_key,
    )
    response = CompletionResponse(
        command_id=operation.command_id,
        status=plan.command_status,
        completed_at=plan.completed_at,
        publications=publications,
    )
    response_bytes = canonical_bytes(response)
    session.add(
        CommandCompletion(
            id=uuid4(),
            command_id=operation.command_id,
            attempt=request.attempt,
            completion_idempotency_key=request.completion_idempotency_key,
            request_hash=request_hash,
            response_payload=response_bytes,
            response_sha256=sha256(response_bytes).hexdigest(),
            response_status=200,
            completed_at=plan.completed_at,
        )
    )
    return CompletionOutcome(200, response, response_bytes)
