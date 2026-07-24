from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

import pytest
from app.analysis.capability import (
    CapabilityApproved,
    CapabilityBlocked,
    CapabilityBlockReason,
)
from app.analysis.output import AnalysisOutput, Sentiment
from app.analysis.queue import (
    LEASE_DURATION,
    RETRY_DELAYS,
    AnalysisBinding,
    LeaseCommand,
    LeaseDenied,
    LeaseGranted,
    QueueError,
    QueueErrorCode,
    QueueItem,
    QueueStatus,
    SuccessAckCommand,
    ack_retryable_failure,
    ack_success,
    heartbeat,
    recover_expired_lease,
    request_lease,
)

NOW = datetime(2026, 7, 22, tzinfo=UTC)
LEASE_VALUE = "lease-value-with-enough-entropy-000000000000"


def _binding() -> AnalysisBinding:
    return AnalysisBinding(
        post_version_id=UUID(int=2),
        content_hash="a" * 64,
        prompt_version="relevance-v1",
        model_version="codex-cli-0.144.1",
        schema_version="analysis-output-v1",
    )


def _item(*, attempts: int = 0) -> QueueItem:
    return QueueItem(
        id=UUID(int=1),
        binding=_binding(),
        status=QueueStatus.PENDING,
        attempts=attempts,
        available_at=NOW,
    )


def _lease_command() -> LeaseCommand:
    return LeaseCommand(
        item_id=UUID(int=1),
        binding=_binding(),
        principal_id=UUID(int=3),
        lease_token=LEASE_VALUE,
        now=NOW,
    )


def test_lease_request_marks_item_blocked_without_issuing_a_token() -> None:
    # Given
    blocked = CapabilityBlocked(
        reasons=(CapabilityBlockReason(code="zero_tools_unproven"),)
    )

    # When
    result = request_lease(_item(), _lease_command(), blocked)

    # Then
    assert isinstance(result, LeaseDenied)
    assert result.item.status is QueueStatus.BLOCKED_CAPABILITY
    assert result.item.attempts == 0
    assert result.reason_codes == ("zero_tools_unproven",)


def test_lease_and_heartbeat_are_bound_to_exact_analysis_tuple() -> None:
    # Given
    approved = CapabilityApproved(proof_set_sha256="b" * 64)

    # When
    result = request_lease(_item(), _lease_command(), approved)

    # Then
    assert isinstance(result, LeaseGranted)
    assert result.item.status is QueueStatus.LEASED
    assert result.item.lease_expires_at == NOW + LEASE_DURATION
    assert result.lease_token == _lease_command().lease_token

    stale = _lease_command().binding.model_copy(update={"content_hash": "c" * 64})
    with pytest.raises(QueueError) as raised:
        _ = heartbeat(
            result.item,
            replace(_lease_command(), binding=stale, now=NOW + timedelta(minutes=1)),
        )
    assert raised.value.code is QueueErrorCode.CAS_MISMATCH


def test_success_ack_persists_only_strict_output_for_same_lease() -> None:
    # Given
    leased = request_lease(
        _item(), _lease_command(), CapabilityApproved(proof_set_sha256="b" * 64)
    )
    assert isinstance(leased, LeaseGranted)
    output = AnalysisOutput(
        relevance=True,
        sentiment=Sentiment.NEGATIVE,
        topics=("regulation",),
    )

    # When
    acknowledged = ack_success(
        leased.item,
        SuccessAckCommand(lease=_lease_command(), output=output),
    )

    # Then
    assert acknowledged.item.status is QueueStatus.SUCCEEDED
    assert acknowledged.analysis.binding == _binding()
    assert acknowledged.analysis.output == output


@pytest.mark.parametrize(
    ("attempts", "expected_status", "expected_delay"),
    [
        (0, QueueStatus.FAILED_RETRYABLE, timedelta(minutes=1)),
        (1, QueueStatus.FAILED_RETRYABLE, timedelta(minutes=5)),
        (2, QueueStatus.FAILED_RETRYABLE, timedelta(minutes=30)),
        (3, QueueStatus.FAILED_TERMINAL, None),
    ],
)
def test_retry_schedule_is_bounded_to_three_retries(
    attempts: int,
    expected_status: QueueStatus,
    expected_delay: timedelta | None,
) -> None:
    # Given
    leased = replace(
        _item(attempts=attempts),
        status=QueueStatus.LEASED,
        lease_token_hash=sha256(_lease_command().lease_token.encode()).digest(),
        lease_expires_at=NOW + LEASE_DURATION,
        leased_by_principal_id=_lease_command().principal_id,
    )

    # When
    failed = ack_retryable_failure(leased, _lease_command(), "invalid_output")

    # Then
    assert failed.status is expected_status
    assert failed.attempts == min(attempts + 1, 3)
    if expected_delay is not None:
        assert failed.available_at == NOW + expected_delay


def test_pc_restart_recovers_expired_lease_and_resumes_same_analysis() -> None:
    approved = CapabilityApproved(proof_set_sha256="b" * 64)
    first = request_lease(_item(), _lease_command(), approved)
    assert isinstance(first, LeaseGranted)
    assert first.item.lease_expires_at is not None

    restart_at = first.item.lease_expires_at + timedelta(hours=1)
    recovered = recover_expired_lease(first.item, restart_at)
    resumed = request_lease(
        recovered,
        replace(
            _lease_command(),
            now=restart_at,
            lease_token=f"{LEASE_VALUE}-after-restart",
        ),
        approved,
    )

    assert recovered.status is QueueStatus.FAILED_RETRYABLE
    assert recovered.attempts == 1
    assert recovered.available_at == first.item.lease_expires_at + RETRY_DELAYS[0]
    assert recovered.binding == first.item.binding
    assert recovered.id == first.item.id
    assert recovered.lease_token_hash is None
    assert isinstance(resumed, LeaseGranted)
    assert resumed.item.binding == first.item.binding
    assert resumed.lease_token != first.lease_token


def test_repeated_expired_leases_keep_retry_schedule_and_max_attempts() -> None:
    approved = CapabilityApproved(proof_set_sha256="b" * 64)
    original = _item()
    current = original

    for expected_attempt, expected_delay in enumerate(RETRY_DELAYS, start=1):
        lease_now = current.available_at
        leased = request_lease(
            current,
            replace(
                _lease_command(),
                now=lease_now,
                lease_token=f"repeated-interruption-lease-{expected_attempt:02d}-000000000",
            ),
            approved,
        )
        assert isinstance(leased, LeaseGranted)
        assert leased.item.lease_expires_at is not None
        current = recover_expired_lease(leased.item, leased.item.lease_expires_at)
        assert current.status is QueueStatus.FAILED_RETRYABLE
        assert current.attempts == expected_attempt
        assert current.available_at == leased.item.lease_expires_at + expected_delay
        assert current.binding == original.binding

    last = request_lease(
        current,
        replace(
            _lease_command(),
            now=current.available_at,
            lease_token=f"{LEASE_VALUE}-terminal",
        ),
        approved,
    )
    assert isinstance(last, LeaseGranted)
    assert last.item.lease_expires_at is not None

    terminal = recover_expired_lease(last.item, last.item.lease_expires_at)

    assert terminal.status is QueueStatus.FAILED_TERMINAL
    assert terminal.attempts == len(RETRY_DELAYS)
    assert terminal.binding == original.binding
    assert terminal.lease_token_hash is None


def test_recovery_rejects_active_lease_without_mutating_it() -> None:
    approved = CapabilityApproved(proof_set_sha256="b" * 64)
    leased = request_lease(_item(), _lease_command(), approved)
    assert isinstance(leased, LeaseGranted)

    active_command = replace(
        _lease_command(),
        now=NOW + timedelta(minutes=1),
        lease_token=f"{LEASE_VALUE}-replacement",
    )
    with pytest.raises(QueueError) as request_raised:
        _ = request_lease(leased.item, active_command, approved)
    with pytest.raises(QueueError) as raised:
        _ = recover_expired_lease(leased.item, NOW + timedelta(minutes=1))

    assert request_raised.value.code is QueueErrorCode.INVALID_STATE
    assert raised.value.code is QueueErrorCode.INVALID_STATE
    assert leased.item.status is QueueStatus.LEASED
    assert leased.item.attempts == 0
