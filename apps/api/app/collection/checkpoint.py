"""Durable checkpoint snapshots, run genesis, and replay guidance."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING

from app.domain.enums import BudgetDecisionStatus, RunStatus, TerminalReason

from .base import (
    CollectionError,
    CollectionErrorCode,
    canonical_json_bytes,
    require_utc,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from app.domain.types import JsonValue


@dataclass(frozen=True, slots=True)
class CheckpointState:
    """Locked cursor and watermark for one source scope."""

    id: UUID
    source_id: UUID
    scope_version: str
    revision: int
    cursor: str | None
    watermark_published_at: datetime | None = None
    watermark_source_post_id: str | None = None
    last_completed_run_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RunStart:
    """Immutable values captured while a command claim locks a checkpoint."""

    run_id: UUID
    command_id: UUID
    source_id: UUID
    scope_version: str
    attempt: int
    lease_identity_hash: bytes
    started_at: datetime


@dataclass(frozen=True, slots=True)
class RunState:
    """Server-owned source-run page stream and finalization facts."""

    id: UUID
    command_id: UUID
    source_id: UUID
    scope_version: str
    attempt: int
    status: RunStatus
    start_checkpoint_revision: int
    start_cursor: str | None
    genesis_chain_hash: str
    committed_page_hash_chain: str
    lease_identity_hash: bytes
    started_at: datetime
    heartbeat_at: datetime
    authorization_decision_id: UUID | None = None
    authorization_snapshot: JsonValue | None = None
    budget_decision_id: UUID | None = None
    budget_decision_status: BudgetDecisionStatus | None = None
    reviewed_page_cap: int | None = None
    reviewed_post_cap: int | None = None
    next_page_ordinal: int = 0
    committed_page_count: int = 0
    accepted_count: int = 0
    duplicate_count: int = 0
    rejected_count: int = 0
    last_page_commit_id: UUID | None = None
    final_page_ordinal: int | None = None
    final_cursor: str | None = None
    terminal_page_commit_id: UUID | None = None
    terminal_page_ordinal: int | None = None
    terminal_cursor: str | None = None
    terminal_reason: TerminalReason | None = None
    terminal_chain_hash: str | None = None
    completion_ready_at: datetime | None = None
    page_reservation_id: UUID | None = None
    skip_authorization_decision_id: UUID | None = None
    skip_budget_decision_id: UUID | None = None
    failure_class: str | None = None
    failure_code: str | None = None
    failure_fingerprint: str | None = None
    failure_observed_at: datetime | None = None
    retry_after_at: datetime | None = None
    finalized_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def skip_decision_id(self) -> UUID | None:
        """Expose only a server-attached skip proof to the workflow."""
        return self.skip_budget_decision_id or self.skip_authorization_decision_id

    @property
    def precomputed_terminal_status(self) -> RunStatus | None:
        """Return the server-owned pre-fetch skip status, when present."""
        if self.skip_budget_decision_id is not None:
            return RunStatus.SKIPPED_QUOTA
        if self.skip_authorization_decision_id is not None:
            return RunStatus.SKIPPED_POLICY
        return None


@dataclass(frozen=True, slots=True)
class CheckpointReplay:
    """Exact persisted resume position returned after a conflict or crash."""

    expected_checkpoint_revision: int
    expected_cursor: str | None
    next_page_ordinal: int
    committed_page_hash_chain: str
    accepted_count: int
    last_page_commit_id: UUID | None


def start_run(start: RunStart, checkpoint: CheckpointState) -> RunState:
    """Create a running attempt with a chain unique to its start snapshot."""
    if (
        start.source_id != checkpoint.source_id
        or start.scope_version != checkpoint.scope_version
    ):
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
    started_at = require_utc(start.started_at)
    identity: JsonValue = {
        "attempt": start.attempt,
        "command_id": str(start.command_id),
        "run_id": str(start.run_id),
        "scope_version": start.scope_version,
        "source_id": str(start.source_id),
        "start_checkpoint_revision": checkpoint.revision,
        "start_cursor": checkpoint.cursor,
    }
    genesis = sha256(
        b"page-chain-genesis/v1\n" + canonical_json_bytes(identity)
    ).hexdigest()
    return RunState(
        id=start.run_id,
        command_id=start.command_id,
        source_id=start.source_id,
        scope_version=start.scope_version,
        attempt=start.attempt,
        status=RunStatus.RUNNING,
        start_checkpoint_revision=checkpoint.revision,
        start_cursor=checkpoint.cursor,
        genesis_chain_hash=genesis,
        committed_page_hash_chain=genesis,
        lease_identity_hash=start.lease_identity_hash,
        started_at=started_at,
        heartbeat_at=started_at,
        final_cursor=checkpoint.cursor,
    )


def checkpoint_replay(run: RunState, checkpoint: CheckpointState) -> CheckpointReplay:
    """Return the only position from which the collector may replay."""
    if (
        run.source_id != checkpoint.source_id
        or run.scope_version != checkpoint.scope_version
        or checkpoint.revision < run.start_checkpoint_revision
    ):
        raise CollectionError(CollectionErrorCode.CHECKPOINT_CONFLICT, 409)
    return CheckpointReplay(
        expected_checkpoint_revision=checkpoint.revision,
        expected_cursor=checkpoint.cursor,
        next_page_ordinal=run.next_page_ordinal,
        committed_page_hash_chain=run.committed_page_hash_chain,
        accepted_count=run.accepted_count,
        last_page_commit_id=run.last_page_commit_id,
    )
