from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from apps.api.app.services.release import _source_activation_sql as activation_sql
from apps.api.scripts import fresh_search_release_gate as release_gate

DB_NOW = datetime(2026, 7, 28, 3, 17, 41, 123456, tzinfo=UTC)
ACTIVATION_NONCE = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ROOT = Path(__file__).resolve().parents[5]
SCRIPT = ROOT / "apps" / "api" / "scripts" / "fresh_search_release_gate.py"


def activation_state(
    *,
    prepared_at: datetime | None = None,
    state: release_gate.ActivationTransition = "handshake_passed",
    active_authorization_id: UUID | None = None,
    current_budget_id: UUID | None = None,
    restore_verified: bool = False,
) -> release_gate.ActivationState:
    actual_prepared_at = prepared_at or DB_NOW - timedelta(minutes=20)
    return release_gate.ActivationState(
        activation_nonce=ACTIVATION_NONCE,
        attestation_generation=1,
        attestation_sha256="a" * 64,
        prepared_at=actual_prepared_at,
        state=state,
        source_enabled=False,
        active_authorization_id=active_authorization_id,
        current_budget_id=current_budget_id,
        current_binding_id=None,
        current_cadence_id=None,
        binding_write_occurred=True,
        restore_verified=restore_verified,
    )


def test_reserve_uses_database_hour_and_binds_exact_provenance() -> None:
    # Given: a finalized, inert generation observed at one PostgreSQL transaction time.
    state = activation_state()
    request = release_gate.ReserveInput(
        db_now=DB_NOW,
        state=state,
        predecessor_sha256="b" * 64,
        handshake_receipt_sha256="c" * 64,
    )

    # When: the activation reservation is planned.
    result = release_gate.plan_reserve(request)

    # Then: the anchor is the DB hour plus three hours and provenance is unchanged.
    assert result.cadence_anchor_at == datetime(2026, 7, 28, 6, tzinfo=UTC)
    assert result.db_now == DB_NOW
    assert result.activation_nonce == ACTIVATION_NONCE
    assert result.attestation_generation == 1
    assert result.attestation_sha256 == "a" * 64
    assert result.predecessor_sha256 == "b" * 64
    assert result.next_state == "anchor_reserved"


@pytest.mark.parametrize(
    ("prepared_at", "accepted"),
    [
        (DB_NOW, True),
        (DB_NOW + timedelta(microseconds=1), False),
        (DB_NOW - timedelta(hours=2) + timedelta(microseconds=1), True),
        (DB_NOW - timedelta(hours=2), False),
    ],
)
def test_reserve_applies_exact_two_hour_database_time_window(
    prepared_at: datetime,
    accepted: bool,
) -> None:
    # Given: preparation at a lower, future, or exclusive upper boundary.
    request = release_gate.ReserveInput(
        db_now=DB_NOW,
        state=activation_state(prepared_at=prepared_at),
        predecessor_sha256="b" * 64,
        handshake_receipt_sha256="c" * 64,
    )

    # When/Then: only prepared_at <= db_now < prepared_at + 2h is accepted.
    if accepted:
        assert release_gate.plan_reserve(request).next_state == "anchor_reserved"
    else:
        with pytest.raises(
            release_gate.ActivationHoldError,
            match="preparation_not_usable",
        ):
            _ = release_gate.plan_reserve(request)


@pytest.mark.parametrize(
    ("db_now", "accepted"),
    [
        (datetime(2026, 7, 28, 4, 59, 59, 999999, tzinfo=UTC), True),
        (datetime(2026, 7, 28, 5, 0, 0, 0, tzinfo=UTC), False),
        (datetime(2026, 7, 28, 5, 0, 0, 1, tzinfo=UTC), False),
    ],
)
def test_commit_enforces_strict_one_hour_anchor_cutoff(
    db_now: datetime,
    accepted: bool,
) -> None:
    # Given: an exact six-o'clock anchor and a DB time around its strict cutoff.
    request = release_gate.CommitInput(
        db_now=db_now,
        state=activation_state(
            prepared_at=db_now - timedelta(minutes=30),
            state="github_finalized",
        ),
        cadence_anchor_at=datetime(2026, 7, 28, 6, tzinfo=UTC),
        predecessor_sha256="b" * 64,
        attestation_receipt_sha256="c" * 64,
        handshake_receipt_sha256="d" * 64,
        finalize_receipt_sha256="e" * 64,
        journal_payload_sha256="f" * 64,
    )

    # When/Then: equality at anchor minus one hour fails closed.
    if accepted:
        result = release_gate.plan_commit(request)
        assert result.accepted
        assert result.effective_at == db_now
        assert result.expires_at == datetime(2026, 8, 28, 6, tzinfo=UTC)
        assert result.next_state == "active"
    else:
        result = release_gate.plan_commit(request)
        assert not result.accepted
        assert result.reason == "activation_cutoff_reached"
        assert result.effective_at is None
        assert result.expires_at is None
        assert result.next_state == "failed"


def test_commit_rejects_provenance_or_non_inert_pointer_drift() -> None:
    # Given: a reserved generation whose current authorization pointer drifted.
    request = release_gate.CommitInput(
        db_now=DB_NOW,
        state=activation_state(
            state="github_finalized",
            active_authorization_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        ),
        cadence_anchor_at=DB_NOW + timedelta(hours=3),
        predecessor_sha256="b" * 64,
        attestation_receipt_sha256="c" * 64,
        handshake_receipt_sha256="d" * 64,
        finalize_receipt_sha256="e" * 64,
        journal_payload_sha256="f" * 64,
    )

    # When/Then: commit cannot create a second or foreign current activation.
    with pytest.raises(release_gate.ActivationHoldError, match="source_not_inert"):
        _ = release_gate.plan_commit(request)


def test_reprepare_requires_next_generation_fresh_evidence_and_verified_restore() -> (
    None
):
    # Given: a failed generation after a binding write and a verified restore.
    request = release_gate.ReprepareInput(
        db_now=DB_NOW,
        state=activation_state(
            state="failed",
            restore_verified=True,
        ),
        requested_generation=2,
        previous_attestation_sha256="a" * 64,
        failed_reservation_sha256="b" * 64,
        fresh_evidence_sha256="c" * 64,
        fresh_evidence_prepared_at=DB_NOW,
        fresh_evidence_activation_nonce=ACTIVATION_NONCE,
    )

    # When: the failed reservation is reprepared without rerunning 0011.
    result = release_gate.plan_reprepare(request)

    # Then: history is preserved and exactly the next generation is prepared.
    assert result.previous_generation == 1
    assert result.attestation_generation == 2
    assert result.previous_attestation_sha256 == "a" * 64
    assert result.failed_reservation_sha256 == "b" * 64
    assert result.attestation_sha256 == "c" * 64
    assert result.next_state == "prepared"


@pytest.mark.parametrize(
    ("updates", "requested_generation", "fresh_sha", "reason"),
    [
        ({}, 3, "c" * 64, "attestation_generation_not_next"),
        ({}, 2, "a" * 64, "activation_evidence_reused"),
        ({"restore_verified": False}, 2, "c" * 64, "binding_restore_required"),
        (
            {"current_budget_id": UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")},
            2,
            "c" * 64,
            "source_not_inert",
        ),
    ],
)
def test_reprepare_rejects_generation_evidence_restore_and_pointer_drift(
    updates: dict[str, bool | UUID],
    requested_generation: int,
    fresh_sha: str,
    reason: str,
) -> None:
    # Given: one invalid reprepare operand.
    state = activation_state(
        state="failed",
        restore_verified=updates.get("restore_verified") is not False,
        current_budget_id=(
            UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
            if "current_budget_id" in updates
            else None
        ),
    )
    request = release_gate.ReprepareInput(
        db_now=DB_NOW,
        state=state,
        requested_generation=requested_generation,
        previous_attestation_sha256="a" * 64,
        failed_reservation_sha256="b" * 64,
        fresh_evidence_sha256=fresh_sha,
        fresh_evidence_prepared_at=DB_NOW,
        fresh_evidence_activation_nonce=ACTIVATION_NONCE,
    )

    # When/Then: no invalid generation can overwrite immutable history.
    with pytest.raises(release_gate.ActivationHoldError, match=reason):
        _ = release_gate.plan_reprepare(request)


def test_restore_helper_never_claims_terminal_restored() -> None:
    # Given: a failed active scope marker.
    # When: the release helper plans the local restore transition sequence.
    result = release_gate.plan_restore(current_state="failed")

    # Then: terminal restored remains owned by the later rollback/privacy finalizer.
    assert result == ("deactivated", "restore_writing")
    assert "restored" not in result


def test_mutating_sql_uses_one_database_clock_and_never_claims_restored() -> None:
    # Given: every final-schema write statement used by the activation command.
    statements = (
        activation_sql.INSERT_CADENCE,
        activation_sql.INSERT_AUTHORIZATION,
        activation_sql.INSERT_BUDGET,
        activation_sql.ACTIVATE_SOURCE,
        activation_sql.INSERT_ATTESTATION,
        activation_sql.ROTATE_INTENT,
        activation_sql.DISABLE_SOURCE,
        activation_sql.INSERT_DEACTIVATED,
        activation_sql.INSERT_TRANSITION,
    )

    # When: their database-time and terminal-state SQL is inspected.
    mutation_sql = "\n".join(str(statement) for statement in statements)

    # Then: one dedicated query owns DB time and every write consumes its bind.
    assert str(activation_sql.DATABASE_NOW).count("transaction_timestamp()") == 1
    assert "pg_advisory_xact_lock" in str(activation_sql.ADVISORY_LOCK)
    assert "transaction_timestamp()" not in mutation_sql
    assert ":db_now" in mutation_sql
    assert "'restored'" not in mutation_sql


def test_activation_budget_uses_the_reviewed_no_spend_thresholds() -> None:
    # Given/When: the activation budget write is rendered.
    statement = str(activation_sql.INSERT_BUDGET)

    # Then: a fresh activation and an idempotent repair both use policy 70/80.
    assert ":effective, :expires, 0, 70, 80" in statement
    assert "soft_stop_units = EXCLUDED.soft_stop_units" in statement
    assert "hard_stop_units = EXCLUDED.hard_stop_units" in statement
    assert "paid_spend_enabled = false" in statement
