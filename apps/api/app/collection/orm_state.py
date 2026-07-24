"""Lossless mappings between mutable ORM rows and collection values."""

from uuid import UUID

from app.db.page_models import PageCommit
from app.db.run_models import CollectionRun, SourceCheckpoint
from app.db.scheduler_models import CollectionCommand

from .base import CollectionError, CollectionErrorCode
from .checkpoint import CheckpointState, RunState
from .commands import CommandState
from .page_commit import PageCommitRecord


def to_command_state(
    row: CollectionCommand,
    source_ids: tuple[UUID, ...],
) -> CommandState:
    """Snapshot one locked command row."""
    return CommandState(
        id=row.id,
        scope_version=row.scope_version,
        source_ids=source_ids,
        kind=row.kind,
        status=row.status,
        attempt=row.attempt,
        available_at=row.available_at,
        reservation_started_at=row.reservation_started_at,
        reservation_nonce_hash=row.reservation_nonce_hash,
        dispatched_at=row.dispatched_at,
        claimed_at=row.claimed_at,
        heartbeat_at=row.heartbeat_at,
        completed_at=row.completed_at,
        lease_hash=row.lease_hash,
        github_run_id=row.github_run_id,
        github_run_attempt=row.github_run_attempt,
        outcome_code=row.outcome_code,
        error_fingerprint=row.error_fingerprint,
    )


def to_checkpoint_state(row: SourceCheckpoint) -> CheckpointState:
    """Snapshot one locked source checkpoint row."""
    return CheckpointState(
        id=row.id,
        source_id=row.source_id,
        scope_version=row.scope_version,
        revision=row.revision,
        cursor=row.cursor,
        watermark_published_at=row.watermark_published_at,
        watermark_source_post_id=row.watermark_source_post_id,
        last_completed_run_id=row.last_completed_run_id,
    )


def to_run_state(row: CollectionRun) -> RunState:
    """Snapshot every completion-effective source-run field."""
    if row.started_at is None or row.heartbeat_at is None:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    return RunState(
        id=row.id,
        command_id=row.command_id,
        source_id=row.source_id,
        scope_version=row.scope_version,
        attempt=row.attempt,
        status=row.status,
        start_checkpoint_revision=row.start_checkpoint_revision,
        start_cursor=row.start_cursor,
        genesis_chain_hash=row.genesis_chain_hash,
        committed_page_hash_chain=row.committed_page_hash_chain,
        lease_identity_hash=row.lease_identity_hash,
        started_at=row.started_at,
        heartbeat_at=row.heartbeat_at,
        authorization_decision_id=row.authorization_decision_id,
        authorization_snapshot=row.authorization_snapshot,
        budget_decision_id=row.budget_decision_id,
        budget_decision_status=row.budget_decision_status,
        reviewed_page_cap=row.reviewed_page_cap,
        reviewed_post_cap=row.reviewed_post_cap,
        next_page_ordinal=row.next_page_ordinal,
        committed_page_count=row.committed_page_count,
        last_page_commit_id=row.last_page_commit_id,
        final_page_ordinal=row.final_page_ordinal,
        final_cursor=row.final_cursor,
        terminal_page_commit_id=row.terminal_page_commit_id,
        terminal_page_ordinal=row.terminal_page_ordinal,
        terminal_cursor=row.terminal_cursor,
        terminal_reason=row.terminal_reason,
        terminal_chain_hash=row.terminal_chain_hash,
        completion_ready_at=row.completion_ready_at,
        page_reservation_id=row.page_reservation_id,
        skip_authorization_decision_id=row.skip_authorization_decision_id,
        skip_budget_decision_id=row.skip_budget_decision_id,
        failure_class=row.failure_class,
        failure_code=row.failure_code,
        failure_fingerprint=row.failure_fingerprint,
        failure_observed_at=row.failure_observed_at,
        retry_after_at=row.retry_after_at,
        finalized_at=row.finalized_at,
        finished_at=row.finished_at,
    )


def to_page_commit_record(row: PageCommit) -> PageCommitRecord:
    """Snapshot one immutable page-chain record."""
    return PageCommitRecord(
        id=row.id,
        run_id=row.run_id,
        command_id=row.command_id,
        attempt=row.attempt,
        lease_identity_hash=row.lease_identity_hash,
        page_idempotency_key=row.page_idempotency_key,
        page_ordinal=row.page_ordinal,
        expected_checkpoint_revision=row.expected_checkpoint_revision,
        resulting_checkpoint_revision=row.resulting_checkpoint_revision,
        expected_cursor=row.expected_cursor,
        next_cursor=row.next_cursor,
        page_request_hash=row.page_request_hash,
        page_content_hash=row.page_content_hash,
        previous_chain_hash=row.previous_chain_hash,
        resulting_chain_hash=row.resulting_chain_hash,
        source_page_receipt_sha256=row.source_page_receipt_sha256,
        source_page_item_count=row.source_page_item_count,
        accepted_count=row.accepted_count,
        duplicate_count=row.duplicate_count,
        rejected_count=row.rejected_count,
        is_terminal_page=row.is_terminal_page,
        terminal_reason=row.terminal_reason,
        stored_response=row.stored_response,
    )


def apply_checkpoint_state(row: SourceCheckpoint, state: CheckpointState) -> None:
    """Apply the sole authorized checkpoint mutation."""
    row.revision = state.revision
    row.cursor = state.cursor
    row.watermark_published_at = state.watermark_published_at
    row.watermark_source_post_id = state.watermark_source_post_id
    row.last_completed_run_id = state.last_completed_run_id


def apply_run_state(row: CollectionRun, state: RunState) -> None:
    """Apply server-derived run counters and lifecycle facts."""
    row.status = state.status
    row.committed_page_hash_chain = state.committed_page_hash_chain
    row.next_page_ordinal = state.next_page_ordinal
    row.committed_page_count = state.committed_page_count
    row.last_page_commit_id = state.last_page_commit_id
    row.final_page_ordinal = state.final_page_ordinal
    row.final_cursor = state.final_cursor
    row.terminal_page_commit_id = state.terminal_page_commit_id
    row.terminal_page_ordinal = state.terminal_page_ordinal
    row.terminal_cursor = state.terminal_cursor
    row.terminal_reason = state.terminal_reason
    row.terminal_chain_hash = state.terminal_chain_hash
    row.completion_ready_at = state.completion_ready_at
    row.failure_class = state.failure_class
    row.failure_code = state.failure_code
    row.failure_fingerprint = state.failure_fingerprint
    row.failure_observed_at = state.failure_observed_at
    row.retry_after_at = state.retry_after_at
    row.finalized_at = state.finalized_at
    row.finished_at = state.finished_at


def apply_command_state(row: CollectionCommand, state: CommandState) -> None:
    """Apply one validated command lifecycle transition."""
    row.status = state.status
    row.attempt = state.attempt
    row.available_at = state.available_at
    row.reservation_started_at = state.reservation_started_at
    row.reservation_nonce_hash = state.reservation_nonce_hash
    row.dispatched_at = state.dispatched_at
    row.claimed_at = state.claimed_at
    row.heartbeat_at = state.heartbeat_at
    row.completed_at = state.completed_at
    row.lease_hash = state.lease_hash
    row.github_run_id = state.github_run_id
    row.github_run_attempt = state.github_run_attempt
    row.outcome_code = state.outcome_code
    row.error_fingerprint = state.error_fingerprint
