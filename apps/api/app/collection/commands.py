"""Typed collection command state and completion aggregation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never

from app.domain.enums import CommandKind, CommandStatus, RunStatus

from .base import CollectionError, CollectionErrorCode, canonical_json_hash

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

type _TerminalBucket = Literal["success", "skip", "retryable", "terminal"]


@dataclass(frozen=True, slots=True)
class CommandState:
    """Durable command facts used by pure transition functions."""

    id: UUID
    scope_version: str
    source_ids: tuple[UUID, ...]
    kind: CommandKind
    status: CommandStatus
    attempt: int
    available_at: datetime
    reservation_started_at: datetime | None = None
    reservation_nonce_hash: bytes | None = None
    dispatched_at: datetime | None = None
    claimed_at: datetime | None = None
    heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    lease_hash: bytes | None = None
    github_run_id: str | None = None
    github_run_attempt: int | None = None
    outcome_code: str | None = None
    error_fingerprint: str | None = None


def collection_source_set_hash(source_ids: tuple[UUID, ...]) -> str:
    """Hash a sorted source identity set for claim binding."""
    return canonical_json_hash(
        [source_id.hex for source_id in sorted(source_ids, key=lambda item: item.hex)]
    )


def aggregate_command_status(statuses: tuple[RunStatus, ...]) -> CommandStatus:
    """Reduce terminal run statuses by the mutually exclusive truth table."""
    if not statuses:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
    buckets = Counter(_terminal_bucket(status) for status in statuses)
    if buckets["success"] == len(statuses):
        return CommandStatus.SUCCEEDED
    if buckets["success"] > 0:
        return CommandStatus.PARTIAL
    if buckets["skip"] == len(statuses):
        return CommandStatus.SKIPPED
    if buckets["retryable"] > 0:
        return CommandStatus.FAILED_RETRYABLE
    return CommandStatus.FAILED_TERMINAL


def _terminal_bucket(status: RunStatus) -> _TerminalBucket:
    match status:
        case RunStatus.SUCCEEDED:
            return "success"
        case RunStatus.SKIPPED_POLICY | RunStatus.SKIPPED_QUOTA:
            return "skip"
        case RunStatus.FAILED_RETRYABLE:
            return "retryable"
        case RunStatus.FAILED_TERMINAL:
            return "terminal"
        case RunStatus.CREATED | RunStatus.RUNNING | RunStatus.STALE_ABANDONED:
            raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
        case _:
            assert_never(status)
