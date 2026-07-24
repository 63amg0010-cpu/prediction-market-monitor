from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.collection.commands import collection_source_set_hash
from app.collection.orm_state import to_command_state, to_run_state
from app.db.run_models import CollectionRun
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import (
    BudgetDecisionStatus,
    CommandKind,
    CommandStatus,
    RunStatus,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
COMMAND_ID = UUID("d470e4ab-6c42-4328-b197-33c1c91ae034")
RUN_ID = UUID("cdb9f94e-776d-4f35-b476-b9b9f1c1aaba")
SOURCE_ID = UUID("8b7a54a9-54bb-481d-9f6f-9357e7f18e37")
AUTHORIZATION_ID = UUID("fda59f90-6937-4771-822c-1df0f51d1552")
BUDGET_ID = UUID("7d9c1e29-39be-4a45-a359-9b958222b475")


def test_command_mapping_keeps_source_policy_on_runs() -> None:
    # Given: command lifecycle state has no source-specific policy columns.
    row = CollectionCommand(
        id=COMMAND_ID,
        slot_id=None,
        scope_version="scope-v1",
        source_set_hash=collection_source_set_hash((SOURCE_ID,)),
        kind=CommandKind.SCHEDULED,
        idempotency_key="scheduled:scope-v1:2026-07-22T12:00:00Z",
        status=CommandStatus.RUNNING,
        attempt=1,
        available_at=NOW,
        claimed_at=NOW,
        heartbeat_at=NOW,
        lease_hash=b"l" * 32,
        created_at=NOW,
    )

    # When: the ORM command is projected into its domain state.
    state = to_command_state(row, (SOURCE_ID,))

    # Then: lifecycle projection succeeds without inventing per-source policy.
    assert state.id == COMMAND_ID
    assert state.source_ids == (SOURCE_ID,)


def test_run_mapping_preserves_claim_authorization_and_budget_scope() -> None:
    # Given: one claimed run with immutable authorization and effective caps.
    authorization = {"decision_id": str(AUTHORIZATION_ID), "status": "approved"}
    row = CollectionRun(
        id=RUN_ID,
        command_id=COMMAND_ID,
        source_id=SOURCE_ID,
        scope_version="scope-v1",
        attempt=1,
        status=RunStatus.RUNNING,
        start_checkpoint_revision=0,
        start_cursor=None,
        genesis_chain_hash="0" * 64,
        committed_page_hash_chain="0" * 64,
        next_page_ordinal=0,
        committed_page_count=0,
        final_cursor=None,
        lease_identity_hash=b"l" * 32,
        authorization_decision_id=AUTHORIZATION_ID,
        authorization_snapshot=authorization,
        budget_decision_id=BUDGET_ID,
        budget_decision_status=BudgetDecisionStatus.SOFT_LIMIT,
        reviewed_page_cap=2,
        reviewed_post_cap=10,
        started_at=NOW,
        heartbeat_at=NOW,
        created_at=NOW,
    )

    # When: later page/finalizer paths reload the durable run.
    state = to_run_state(row)

    # Then: the exact claim identities and server caps survive the round trip.
    assert state.authorization_decision_id == AUTHORIZATION_ID
    assert state.authorization_snapshot == authorization
    assert state.budget_decision_id == BUDGET_ID
    assert state.budget_decision_status is BudgetDecisionStatus.SOFT_LIMIT
    assert state.reviewed_page_cap == 2
    assert state.reviewed_post_cap == 10
