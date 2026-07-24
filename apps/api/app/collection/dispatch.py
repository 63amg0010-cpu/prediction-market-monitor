"""Deterministic dispatch, lease heartbeat, and stale transitions."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hmac import digest
from uuid import UUID

from app.domain.enums import CommandStatus

from .base import (
    EXECUTION_STALE_SECONDS,
    MAX_COMMAND_ATTEMPTS,
    RESERVATION_STALE_SECONDS,
    CollectionError,
    CollectionErrorCode,
    hash_token,
    require_utc,
    token_matches,
)
from .commands import CommandState

SECOND_ATTEMPT = 2
THIRD_ATTEMPT = 3


@dataclass(frozen=True, slots=True)
class DispatchReservation:
    """Plaintext one-use values returned only to the reserving actor."""

    reservation_nonce: str
    lease_token: str


@dataclass(frozen=True, slots=True)
class DispatchConfirmation:
    """Workflow acceptance facts bound to one reserved attempt."""

    attempt: int
    reservation_nonce: str
    github_run_id: str
    github_run_attempt: int


@dataclass(frozen=True, slots=True)
class ClaimCredentials:
    """Attempt, reservation, and lease proof supplied by a collector."""

    attempt: int
    lease_token: str
    reservation_nonce: str


@dataclass(frozen=True, slots=True)
class StaleCheck:
    """Database-time stale reconciliation inputs."""

    db_now: datetime
    completion_ready: bool
    retry_jitter_key: bytes


def retry_delay_seconds(key: bytes, command_id: UUID, next_attempt: int) -> int:
    """Derive inclusive deterministic HMAC retry jitter."""
    message = f"{command_id}:{next_attempt}".encode()
    value = int.from_bytes(digest(key, message, "sha256")[:8], "big")
    if next_attempt == SECOND_ATTEMPT:
        return 30 + value % 16
    if next_attempt == THIRD_ATTEMPT:
        return 120 + value % 31
    raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)


def reserve_dispatch(
    command: CommandState,
    reservation: DispatchReservation,
    db_now: datetime,
) -> CommandState:
    """Reserve a due first or retry attempt and bind one-use secrets."""
    now = require_utc(db_now)
    if command.available_at > now:
        raise CollectionError(CollectionErrorCode.COMMAND_NOT_AVAILABLE, 409)
    if command.reservation_nonce_hash is not None or command.lease_hash is not None:
        raise CollectionError(CollectionErrorCode.RESERVATION_ACTIVE, 409)
    if command.status not in (CommandStatus.QUEUED, CommandStatus.FAILED_RETRYABLE):
        raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
    next_attempt = command.attempt + int(
        command.status is CommandStatus.FAILED_RETRYABLE
    )
    if next_attempt > MAX_COMMAND_ATTEMPTS:
        raise CollectionError(CollectionErrorCode.RETRIES_EXHAUSTED, 409)
    return replace(
        command,
        status=CommandStatus.DISPATCH_RESERVED,
        attempt=next_attempt,
        reservation_started_at=now,
        reservation_nonce_hash=hash_token(reservation.reservation_nonce),
        lease_hash=hash_token(reservation.lease_token),
    )


def confirm_dispatch(
    command: CommandState,
    confirmation: DispatchConfirmation,
    db_now: datetime,
) -> CommandState:
    """Record GitHub acceptance only for the matching reservation."""
    valid = (
        command.status is CommandStatus.DISPATCH_RESERVED
        and command.attempt == confirmation.attempt
        and token_matches(
            confirmation.reservation_nonce,
            command.reservation_nonce_hash,
        )
    )
    if not valid:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    if not confirmation.github_run_id or confirmation.github_run_attempt < 1:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
    return replace(
        command,
        status=CommandStatus.DISPATCHED,
        dispatched_at=require_utc(db_now),
        github_run_id=confirmation.github_run_id,
        github_run_attempt=confirmation.github_run_attempt,
    )


def claim_command(
    command: CommandState,
    credentials: ClaimCredentials,
    db_now: datetime,
) -> CommandState:
    """Claim a reserved or confirmed dispatch with both one-use proofs."""
    proofs_match = (
        command.attempt == credentials.attempt
        and token_matches(credentials.lease_token, command.lease_hash)
        and token_matches(
            credentials.reservation_nonce,
            command.reservation_nonce_hash,
        )
    )
    if command.status in (
        CommandStatus.DISPATCH_RESERVED,
        CommandStatus.DISPATCHED,
    ):
        if not proofs_match:
            raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
        now = require_utc(db_now)
        return replace(
            command,
            status=CommandStatus.RUNNING,
            claimed_at=now,
            heartbeat_at=now,
        )
    if command.status is CommandStatus.RUNNING:
        if proofs_match:
            return command
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)


def heartbeat_command(
    command: CommandState,
    credentials: ClaimCredentials,
    db_now: datetime,
) -> CommandState:
    """Advance a running command heartbeat when lease and attempt match."""
    valid = (
        command.status is CommandStatus.RUNNING
        and command.attempt == credentials.attempt
        and token_matches(credentials.lease_token, command.lease_hash)
    )
    if not valid:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    return replace(command, heartbeat_at=require_utc(db_now))


def mark_stale(command: CommandState, check: StaleCheck) -> CommandState:
    """Apply exact reservation, dispatch, and heartbeat stale anchors."""
    now = require_utc(check.db_now)
    if command.status is CommandStatus.DISPATCH_RESERVED:
        return _reconcile_reservation(command, now, check.retry_jitter_key)
    if command.status is CommandStatus.STALE_ABANDONED:
        return _release_abandoned(command, now, check.retry_jitter_key)
    anchor = None
    if command.status is CommandStatus.DISPATCHED:
        anchor = command.dispatched_at
    if command.status is CommandStatus.RUNNING:
        if check.completion_ready:
            return command
        anchor = command.heartbeat_at or command.claimed_at
    if anchor is None or (now - anchor).total_seconds() <= EXECUTION_STALE_SECONDS:
        return command
    return replace(command, status=CommandStatus.STALE_ABANDONED)


def _release_abandoned(
    command: CommandState, now: datetime, retry_jitter_key: bytes
) -> CommandState:
    terminal = command.attempt >= MAX_COMMAND_ATTEMPTS
    available = now
    if not terminal:
        available += timedelta(
            seconds=retry_delay_seconds(
                retry_jitter_key,
                command.id,
                command.attempt + 1,
            )
        )
    return replace(
        command,
        status=(
            CommandStatus.FAILED_TERMINAL
            if terminal
            else CommandStatus.FAILED_RETRYABLE
        ),
        available_at=available,
        reservation_started_at=None,
        reservation_nonce_hash=None,
        dispatched_at=None,
        claimed_at=None,
        heartbeat_at=None,
        lease_hash=None,
        github_run_id=None,
        github_run_attempt=None,
    )


def _reconcile_reservation(
    command: CommandState, now: datetime, retry_jitter_key: bytes
) -> CommandState:
    anchor = command.reservation_started_at
    if anchor is None or (now - anchor).total_seconds() <= RESERVATION_STALE_SECONDS:
        return command
    return _release_abandoned(command, now, retry_jitter_key)
