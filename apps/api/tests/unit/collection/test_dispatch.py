from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.collection.base import CollectionError, CollectionErrorCode, hash_token
from app.collection.commands import CommandState
from app.collection.dispatch import (
    ClaimCredentials,
    DispatchConfirmation,
    DispatchReservation,
    StaleCheck,
    claim_command,
    confirm_dispatch,
    heartbeat_command,
    mark_stale,
    reserve_dispatch,
    retry_delay_seconds,
)
from app.domain.enums import CommandKind, CommandStatus

NOW = datetime(2026, 7, 20, tzinfo=UTC)


def command(status: CommandStatus, attempt: int = 1) -> CommandState:
    return CommandState(
        id=uuid4(),
        scope_version="scope-v1",
        source_ids=(uuid4(),),
        kind=CommandKind.SCHEDULED,
        status=status,
        attempt=attempt,
        available_at=NOW,
    )


def test_retry_jitter_is_deterministic_and_inside_exact_ranges() -> None:
    # Given: one command and a fixed server-only jitter key.
    command_id = uuid4()
    key = b"k" * 32

    # When: both retry delays are derived twice.
    delays = (
        retry_delay_seconds(key, command_id, 2),
        retry_delay_seconds(key, command_id, 3),
    )

    # Then: they are stable and stay in the inclusive normative ranges.
    assert delays == (
        retry_delay_seconds(key, command_id, 2),
        retry_delay_seconds(key, command_id, 3),
    )
    assert 30 <= delays[0] <= 45
    assert 120 <= delays[1] <= 150


def test_retry_reservation_consumes_next_attempt_and_binds_secrets() -> None:
    # Given: a retryable command whose deterministic delay is already due.
    current = command(CommandStatus.FAILED_RETRYABLE)
    credentials = DispatchReservation("nonce", "lease")

    # When: the server reserves its next dispatch.
    reserved = reserve_dispatch(current, credentials, NOW)

    # Then: attempt two is consumed and only hashes enter durable state.
    assert reserved.status is CommandStatus.DISPATCH_RESERVED
    assert reserved.attempt == 2
    assert reserved.reservation_nonce_hash == hash_token("nonce")
    assert reserved.lease_hash == hash_token("lease")


def test_heartbeat_rejects_wrong_attempt_or_lease() -> None:
    # Given: a claimed command bound to attempt one and one lease.
    credential = "lease"
    current = command(CommandStatus.RUNNING)
    current = replace(
        current,
        claimed_at=NOW,
        heartbeat_at=NOW,
        lease_hash=hash_token(credential),
    )

    # When/Then: a superseded attempt cannot extend the lease.
    with pytest.raises(CollectionError) as captured:
        _ = heartbeat_command(
            current,
            ClaimCredentials(
                attempt=2,
                lease_token=credential,
                reservation_nonce="n",
            ),
            NOW + timedelta(minutes=1),
        )
    assert captured.value.code is CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH


def test_confirmed_dispatch_claims_only_matching_nonce_and_lease() -> None:
    # Given: a due command reserved with one nonce and lease.
    reserved = reserve_dispatch(
        command(CommandStatus.QUEUED),
        DispatchReservation("nonce", "lease"),
        NOW,
    )

    # When: GitHub acceptance is confirmed and that workflow claims the command.
    dispatched = confirm_dispatch(
        reserved,
        DispatchConfirmation(1, "nonce", "run-1", 1),
        NOW + timedelta(seconds=1),
    )
    claimed = claim_command(
        dispatched,
        ClaimCredentials(1, "lease", "nonce"),
        NOW + timedelta(seconds=2),
    )

    # Then: durable anchors retain the accepted workflow and active lease.
    assert dispatched.status is CommandStatus.DISPATCHED
    assert dispatched.github_run_id == "run-1"
    assert claimed.status is CommandStatus.RUNNING
    assert claimed.claimed_at == NOW + timedelta(seconds=2)
    assert claimed.heartbeat_at == claimed.claimed_at


def test_claim_rejects_nonce_mismatch_without_transition() -> None:
    # Given: a dispatch reservation owned by one workflow nonce.
    reserved = reserve_dispatch(
        command(CommandStatus.QUEUED),
        DispatchReservation("nonce", "lease"),
        NOW,
    )

    # When/Then: a different workflow nonce cannot claim its lease.
    with pytest.raises(CollectionError) as captured:
        _ = claim_command(
            reserved,
            ClaimCredentials(1, "lease", "other"),
            NOW + timedelta(seconds=1),
        )
    assert captured.value.code is CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH


@pytest.mark.parametrize(
    ("status", "anchor_field", "age", "completion_ready", "expected"),
    [
        (
            CommandStatus.DISPATCH_RESERVED,
            "reservation",
            181,
            False,
            CommandStatus.FAILED_RETRYABLE,
        ),
        (
            CommandStatus.DISPATCH_RESERVED,
            "reservation",
            180,
            False,
            CommandStatus.DISPATCH_RESERVED,
        ),
        (
            CommandStatus.DISPATCHED,
            "dispatch",
            361,
            False,
            CommandStatus.STALE_ABANDONED,
        ),
        (
            CommandStatus.RUNNING,
            "heartbeat",
            361,
            False,
            CommandStatus.STALE_ABANDONED,
        ),
        (
            CommandStatus.RUNNING,
            "heartbeat",
            361,
            True,
            CommandStatus.RUNNING,
        ),
    ],
)
def test_stale_detection_uses_exact_anchors(
    status: CommandStatus,
    anchor_field: str,
    age: int,
    completion_ready: bool,
    expected: CommandStatus,
) -> None:
    # Given: a command at a precise database-time age boundary.
    anchor = NOW - timedelta(seconds=age)
    current = replace(
        command(status),
        reservation_started_at=anchor if anchor_field == "reservation" else None,
        dispatched_at=anchor if anchor_field == "dispatch" else None,
        claimed_at=anchor if anchor_field == "heartbeat" else None,
        heartbeat_at=anchor if anchor_field == "heartbeat" else None,
        lease_hash=hash_token("lease") if status is CommandStatus.RUNNING else None,
    )

    # When: reconciliation evaluates it using database time.
    reconciled = mark_stale(
        current,
        StaleCheck(
            db_now=NOW,
            completion_ready=completion_ready,
            retry_jitter_key=b"k" * 32,
        ),
    )

    # Then: only strictly exceeded anchors abandon the command.
    assert reconciled.status is expected


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [
        (1, CommandStatus.FAILED_RETRYABLE),
        (3, CommandStatus.FAILED_TERMINAL),
    ],
)
def test_abandoned_command_reconciliation_restores_a_reservable_state(
    attempt: int, expected: CommandStatus
) -> None:
    # Given: a prior reconciliation persisted an abandoned command.
    current = replace(
        command(CommandStatus.STALE_ABANDONED, attempt),
        reservation_nonce_hash=hash_token("nonce"),
        lease_hash=hash_token("lease"),
    )

    # When: the next database-time reconciliation evaluates it.
    reconciled = mark_stale(
        current,
        StaleCheck(
            db_now=NOW,
            completion_ready=False,
            retry_jitter_key=b"k" * 32,
        ),
    )

    # Then: retryable work is schedulable and exhausted work is terminal.
    assert reconciled.status is expected
    assert reconciled.reservation_nonce_hash is None
    assert reconciled.lease_hash is None
    if expected is CommandStatus.FAILED_RETRYABLE:
        assert reconciled.available_at > NOW
    else:
        assert reconciled.available_at == NOW
