"""Persist immutable verifier snapshots before accepting observations."""

from collections.abc import Sequence

from alembic import op
from app.db.models import metadata
from sqlalchemy.schema import CreateTable

revision: str = "20260722_0003"
down_revision: str | None = "20260722_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADD_OBSERVATION_SNAPSHOT_FK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_verification_observations_snapshot'
    ) THEN
        ALTER TABLE verification_observations
        ADD CONSTRAINT fk_verification_observations_snapshot
        FOREIGN KEY (snapshot_id)
        REFERENCES verification_snapshots (id)
        ON DELETE RESTRICT;
    END IF;
END
$$
"""

_CREATE_IMMUTABLE_TRIGGERS = (
    """
    DROP TRIGGER IF EXISTS trg_verification_snapshots_immutable
    ON verification_snapshots
    """,
    """
    CREATE TRIGGER trg_verification_snapshots_immutable
    BEFORE UPDATE ON verification_snapshots
    FOR EACH ROW EXECUTE FUNCTION monitor_reject_update()
    """,
    """
    DROP TRIGGER IF EXISTS trg_verification_snapshot_sources_immutable
    ON verification_snapshot_sources
    """,
    """
    CREATE TRIGGER trg_verification_snapshot_sources_immutable
    BEFORE UPDATE ON verification_snapshot_sources
    FOR EACH ROW EXECUTE FUNCTION monitor_reject_update()
    """,
    """
    DROP TRIGGER IF EXISTS trg_verification_snapshot_uses_immutable
    ON verification_snapshot_uses
    """,
    """
    CREATE TRIGGER trg_verification_snapshot_uses_immutable
    BEFORE UPDATE ON verification_snapshot_uses
    FOR EACH ROW EXECUTE FUNCTION monitor_reject_update()
    """,
)


def upgrade() -> None:
    """Create durable snapshot headers, source facts, and observation binding."""
    for table in (
        metadata.tables["verification_snapshots"],
        metadata.tables["verification_snapshot_sources"],
        metadata.tables["verification_snapshot_uses"],
    ):
        op.execute(CreateTable(table, if_not_exists=True))
    op.execute(_ADD_OBSERVATION_SNAPSHOT_FK)
    for statement in _CREATE_IMMUTABLE_TRIGGERS:
        op.execute(statement)


def downgrade() -> None:
    """Remove snapshot binding and its immutable durable facts."""
    op.drop_constraint(
        "fk_verification_observations_snapshot",
        "verification_observations",
        type_="foreignkey",
    )
    op.drop_table("verification_snapshot_uses", if_exists=True)
    op.drop_table("verification_snapshot_sources", if_exists=True)
    op.drop_table("verification_snapshots", if_exists=True)
