"""Atomic PostgreSQL page-commit persistence."""

from hashlib import sha256
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.page_models import PageCommit

from .base import require_utc
from .orm_state import apply_checkpoint_state, apply_run_state
from .page_commit import PageCommitOutcome, prepare_page_commit
from .page_context_store import load_locked_page_context
from .page_service_models import PageCommitOperation, PageCommitServiceConfig
from .post_store import PageWriteContext, persist_page_items


async def execute_page_commit(
    session: AsyncSession,
    operation: PageCommitOperation,
    config: PageCommitServiceConfig,
) -> PageCommitOutcome:
    """Plan and persist one page inside the caller-owned transaction."""
    locked = await load_locked_page_context(session, operation, config)
    plan = prepare_page_commit(locked.domain, operation.request, uuid4)
    if not plan.should_persist:
        return plan.outcome
    request = operation.request
    commit = plan.commit
    session.add(
        PageCommit(
            id=commit.id,
            run_id=commit.run_id,
            checkpoint_id=locked.checkpoint_row.id,
            command_id=commit.command_id,
            attempt=commit.attempt,
            lease_identity_hash=commit.lease_identity_hash,
            page_idempotency_key=commit.page_idempotency_key,
            page_ordinal=commit.page_ordinal,
            expected_checkpoint_revision=commit.expected_checkpoint_revision,
            resulting_checkpoint_revision=commit.resulting_checkpoint_revision,
            expected_cursor=commit.expected_cursor,
            next_cursor=commit.next_cursor,
            page_request_hash=commit.page_request_hash,
            page_content_hash=commit.page_content_hash,
            previous_chain_hash=commit.previous_chain_hash,
            resulting_chain_hash=commit.resulting_chain_hash,
            source_page_receipt_sha256=commit.source_page_receipt_sha256,
            source_page_item_count=commit.source_page_item_count,
            accepted_count=commit.accepted_count,
            duplicate_count=commit.duplicate_count,
            rejected_count=commit.rejected_count,
            page_fetch_started_at=require_utc(request.page_fetch_started_at),
            page_fetch_finished_at=require_utc(request.page_fetch_finished_at),
            is_terminal_page=commit.is_terminal_page,
            terminal_reason=commit.terminal_reason,
            stored_response=commit.stored_response,
            stored_response_sha256=sha256(commit.stored_response).hexdigest(),
            response_status=201,
            committed_at=locked.domain.db_now,
        )
    )
    await persist_page_items(
        session,
        PageWriteContext(
            locked.run_row.id,
            locked.run_row.source_id,
            require_utc(request.page_fetch_finished_at),
            config.analysis_versions,
        ),
        plan,
    )
    apply_checkpoint_state(locked.checkpoint_row, plan.updated_checkpoint)
    apply_run_state(locked.run_row, plan.updated_run)
    return plan.outcome
