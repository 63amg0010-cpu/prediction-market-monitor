from datetime import UTC, datetime
from uuid import UUID

from app.collection.base import hash_token
from app.collection.checkpoint import (
    CheckpointState,
    RunStart,
    checkpoint_replay,
    start_run,
)

NOW = datetime(2026, 7, 20, tzinfo=UTC)


def test_run_start_snapshots_checkpoint_and_resets_chain() -> None:
    # Given: a persisted checkpoint advanced by an earlier attempt.
    checkpoint = CheckpointState(
        id=UUID(int=1),
        source_id=UUID(int=2),
        scope_version="scope-v1",
        revision=7,
        cursor="cursor-7",
    )
    start = RunStart(
        run_id=UUID(int=3),
        command_id=UUID(int=4),
        source_id=checkpoint.source_id,
        scope_version="scope-v1",
        attempt=2,
        lease_identity_hash=hash_token("l" * 43),
        started_at=NOW,
    )

    # When: the retry creates a new run.
    run = start_run(start, checkpoint)

    # Then: ordinal and chain restart while cursor/revision resume durably.
    assert run.start_checkpoint_revision == 7
    assert run.start_cursor == "cursor-7"
    assert run.next_page_ordinal == 0
    assert run.committed_page_count == 0
    assert run.committed_page_hash_chain == run.genesis_chain_hash


def test_checkpoint_replay_returns_only_persisted_progress() -> None:
    # Given: a run with one durable page and its matching checkpoint.
    checkpoint = CheckpointState(
        id=UUID(int=1),
        source_id=UUID(int=2),
        scope_version="scope-v1",
        revision=8,
        cursor="cursor-8",
    )
    run = start_run(
        RunStart(
            UUID(int=3),
            UUID(int=4),
            checkpoint.source_id,
            "scope-v1",
            2,
            hash_token("l" * 43),
            NOW,
        ),
        checkpoint,
    )

    # When: a collector reloads after interruption.
    replay = checkpoint_replay(run, checkpoint)

    # Then: it must use server cursor/revision/ordinal rather than guessing.
    assert replay.expected_checkpoint_revision == 8
    assert replay.expected_cursor == "cursor-8"
    assert replay.next_page_ordinal == 0
    assert replay.committed_page_hash_chain == run.genesis_chain_hash
