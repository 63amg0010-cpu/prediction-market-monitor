from pathlib import Path

from app.db import Base, models
from sqlalchemy import LargeBinary, UniqueConstraint


def test_verification_snapshot_tables_retain_exact_immutable_server_facts() -> None:
    # Given: all production ORM models are registered.
    assert models is not None

    # When: the verifier snapshot tables are inspected.
    snapshots = Base.metadata.tables["verification_snapshots"]
    sources = Base.metadata.tables["verification_snapshot_sources"]
    uses = Base.metadata.tables["verification_snapshot_uses"]
    observations = Base.metadata.tables["verification_observations"]

    # Then: canonical bytes and source facts survive a process restart.
    assert isinstance(snapshots.c.canonical_payload.type, LargeBinary)
    assert snapshots.c.published_at.nullable is False
    assert snapshots.c.snapshot_checksum.nullable is False
    assert sources.c.snapshot_id.foreign_keys
    assert sources.c.latest_successful_run_finished_at.nullable is True
    assert sources.c.visible_publication_sequence.nullable is True
    assert sources.c.publication_first_visible_at.nullable is True
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns}
        == {"snapshot_id", "source_id"}
        for constraint in sources.constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"snapshot_id"}
        for constraint in uses.constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns}
        == {"scope_version", "expected_slot_utc", "source_id"}
        for constraint in observations.constraints
    )
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns}
        == {"scope_version", "expected_slot_utc"}
        for constraint in uses.constraints
    )


def test_verification_snapshot_migration_follows_tombstone_revision() -> None:
    # Given: the coordinated Phase 2 verifier migration path.
    migration = (
        Path(__file__).parents[2]
        / "migrations"
        / "versions"
        / "20260722_0003_verification_snapshots.py"
    )

    # When/Then: the durable schema revision exists after 0002.
    text = migration.read_text(encoding="utf-8")
    assert 'revision: str = "20260722_0003"' in text
    assert 'down_revision: str | None = "20260722_0002"' in text
