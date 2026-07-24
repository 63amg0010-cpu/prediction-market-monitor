"""Version-bound analysis queue transitions."""

import hmac
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum, unique
from hashlib import sha256
from typing import ClassVar, assert_never, override
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import QueueStatus

from .capability import CapabilityApproved, CapabilityBlocked, CapabilityDecision
from .output import AnalysisOutput

LEASE_DURATION = timedelta(minutes=10)
RETRY_DELAYS: tuple[timedelta, ...] = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=30),
)
MINIMUM_LEASE_CREDENTIAL_LENGTH = 32


class AnalysisBinding(BaseModel):
    """Immutable work identity checked by every queue mutation."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", strict=True
    )

    post_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = Field(min_length=1, max_length=100)
    model_version: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(min_length=1, max_length=100)


@dataclass(frozen=True, slots=True)
class QueueItem:
    """Server-owned state for one post-version analysis tuple."""

    id: UUID
    binding: AnalysisBinding
    status: QueueStatus
    attempts: int
    available_at: datetime
    lease_token_hash: bytes | None = None
    lease_expires_at: datetime | None = None
    leased_by_principal_id: UUID | None = None
    last_error_code: str | None = None


@dataclass(frozen=True, slots=True)
class LeaseCommand:
    """CAS tuple and lease identity supplied by the worker boundary."""

    item_id: UUID
    binding: AnalysisBinding
    principal_id: UUID
    lease_token: str
    now: datetime


@dataclass(frozen=True, slots=True)
class LeaseGranted:
    """Leased state plus the one response-only raw token."""

    item: QueueItem
    lease_token: str


@dataclass(frozen=True, slots=True)
class LeaseDenied:
    """Capability-blocked state that contains no lease token."""

    item: QueueItem
    reason_codes: tuple[str, ...]


type LeaseResult = LeaseGranted | LeaseDenied


@dataclass(frozen=True, slots=True)
class SuccessAckCommand:
    """Strict output attached to the original lease command."""

    lease: LeaseCommand
    output: AnalysisOutput


@dataclass(frozen=True, slots=True)
class AnalysisRecord:
    """Immutable valid analysis prepared by a successful CAS ack."""

    binding: AnalysisBinding
    output: AnalysisOutput
    output_hash: str
    analyzed_at: datetime


@dataclass(frozen=True, slots=True)
class SuccessAcknowledged:
    """Atomic queue and analysis outcome."""

    item: QueueItem
    analysis: AnalysisRecord


@unique
class QueueErrorCode(StrEnum):
    """Stable queue conflict reasons."""

    CAS_MISMATCH = "cas_mismatch"
    NOT_AVAILABLE = "not_available"
    LEASE_MISMATCH = "lease_mismatch"
    LEASE_EXPIRED = "lease_expired"
    INVALID_STATE = "invalid_state"
    INVALID_LEASE_CREDENTIAL = "invalid_lease_credential"


@dataclass(frozen=True, slots=True)
class QueueError(Exception):
    """Typed queue conflict without content or token disclosure."""

    code: QueueErrorCode

    @override
    def __str__(self) -> str:
        return self.code.value


def request_lease(
    item: QueueItem,
    command: LeaseCommand,
    decision: CapabilityDecision,
) -> LeaseResult:
    """Refuse before leasing unless the complete capability AND gate passed."""
    match decision:  # noqa: RUF100  # noqa: MATCH_OK
        case CapabilityBlocked(reasons=reasons):
            return LeaseDenied(
                item=replace(
                    item,
                    status=QueueStatus.BLOCKED_CAPABILITY,
                    last_error_code="blocked_capability",
                ),
                reason_codes=tuple(reason.code for reason in reasons),
            )
        case CapabilityApproved():
            return _grant_lease(item, command)
    assert_never(decision)


def _grant_lease(item: QueueItem, command: LeaseCommand) -> LeaseGranted:
    _require_cas(item, command)
    _require_leaseable(item.status)
    if item.available_at > command.now:
        raise QueueError(QueueErrorCode.NOT_AVAILABLE)
    if len(command.lease_token) < MINIMUM_LEASE_CREDENTIAL_LENGTH:
        raise QueueError(QueueErrorCode.INVALID_LEASE_CREDENTIAL)
    leased = replace(
        item,
        status=QueueStatus.LEASED,
        lease_token_hash=sha256(command.lease_token.encode()).digest(),
        lease_expires_at=command.now + LEASE_DURATION,
        leased_by_principal_id=command.principal_id,
        last_error_code=None,
    )
    return LeaseGranted(leased, command.lease_token)


def _require_leaseable(status: QueueStatus) -> None:
    match status:  # noqa: RUF100  # noqa: MATCH_OK
        case QueueStatus.PENDING | QueueStatus.FAILED_RETRYABLE:
            return
        case (
            QueueStatus.LEASED
            | QueueStatus.SUCCEEDED
            | QueueStatus.BLOCKED_CAPABILITY
            | QueueStatus.FAILED_TERMINAL
        ):
            raise QueueError(QueueErrorCode.INVALID_STATE)
    assert_never(status)


def heartbeat(item: QueueItem, command: LeaseCommand) -> QueueItem:
    """Extend an active lease only for its exact CAS tuple and owner."""
    _require_active_lease(item, command)
    return replace(item, lease_expires_at=command.now + LEASE_DURATION)


def ack_success(item: QueueItem, command: SuccessAckCommand) -> SuccessAcknowledged:
    """Atomically prepare immutable analysis and terminal queue state."""
    _require_active_lease(item, command.lease)
    output_hash = sha256(command.output.model_dump_json().encode()).hexdigest()
    analysis = AnalysisRecord(
        binding=item.binding,
        output=command.output,
        output_hash=output_hash,
        analyzed_at=command.lease.now,
    )
    return SuccessAcknowledged(
        item=_clear_lease(item, QueueStatus.SUCCEEDED, command.lease.now, None),
        analysis=analysis,
    )


def ack_retryable_failure(
    item: QueueItem, command: LeaseCommand, error_code: str
) -> QueueItem:
    """Schedule 1m, 5m, and 30m retries, then terminalize."""
    _require_active_lease(item, command)
    return _schedule_retry(item, command.now, error_code)


def recover_expired_lease(item: QueueItem, now: datetime) -> QueueItem:
    """Atomically reclaim an expired lease into the bounded retry schedule."""
    if item.status is not QueueStatus.LEASED:
        raise QueueError(QueueErrorCode.INVALID_STATE)
    if (
        item.lease_token_hash is None
        or item.lease_expires_at is None
        or item.leased_by_principal_id is None
    ):
        raise QueueError(QueueErrorCode.LEASE_MISMATCH)
    if item.lease_expires_at > now:
        raise QueueError(QueueErrorCode.INVALID_STATE)
    return _schedule_retry(item, item.lease_expires_at, "lease_expired")


def _schedule_retry(
    item: QueueItem, interrupted_at: datetime, error_code: str
) -> QueueItem:
    if item.attempts >= len(RETRY_DELAYS):
        return _clear_lease(
            item, QueueStatus.FAILED_TERMINAL, interrupted_at, error_code
        )
    attempts = item.attempts + 1
    delay = RETRY_DELAYS[attempts - 1]
    return replace(
        _clear_lease(
            item, QueueStatus.FAILED_RETRYABLE, interrupted_at + delay, error_code
        ),
        attempts=attempts,
    )


def _require_cas(item: QueueItem, command: LeaseCommand) -> None:
    if item.id != command.item_id or item.binding != command.binding:
        raise QueueError(QueueErrorCode.CAS_MISMATCH)


def _require_active_lease(item: QueueItem, command: LeaseCommand) -> None:
    _require_cas(item, command)
    if item.status is not QueueStatus.LEASED:
        raise QueueError(QueueErrorCode.INVALID_STATE)
    if (
        item.lease_token_hash is None
        or item.lease_expires_at is None
        or item.leased_by_principal_id != command.principal_id
    ):
        raise QueueError(QueueErrorCode.LEASE_MISMATCH)
    supplied = sha256(command.lease_token.encode()).digest()
    if not hmac.compare_digest(item.lease_token_hash, supplied):
        raise QueueError(QueueErrorCode.LEASE_MISMATCH)
    if item.lease_expires_at <= command.now:
        raise QueueError(QueueErrorCode.LEASE_EXPIRED)


def _clear_lease(
    item: QueueItem,
    status: QueueStatus,
    available_at: datetime,
    error_code: str | None,
) -> QueueItem:
    return replace(
        item,
        status=status,
        available_at=available_at,
        lease_token_hash=None,
        lease_expires_at=None,
        leased_by_principal_id=None,
        last_error_code=error_code,
    )


__all__ = [
    "LEASE_DURATION",
    "AnalysisBinding",
    "LeaseCommand",
    "LeaseDenied",
    "LeaseGranted",
    "QueueError",
    "QueueErrorCode",
    "QueueItem",
    "QueueStatus",
    "SuccessAckCommand",
    "ack_retryable_failure",
    "ack_success",
    "heartbeat",
    "recover_expired_lease",
    "request_lease",
]
