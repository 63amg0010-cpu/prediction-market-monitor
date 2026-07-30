"""Persist claim authorization, budget scope, and skip observations."""

from collections.abc import Sequence

from alembic import op
from frozen_migration_snapshots.revision_0004 import TABLE
from sqlalchemy.schema import CreateTable

revision: str = "20260722_0004"
down_revision: str | None = "20260722_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADD_COLUMNS = (
    """ALTER TABLE collection_runs
    ADD COLUMN IF NOT EXISTS authorization_decision_id UUID""",
    """ALTER TABLE collection_runs
    ADD COLUMN IF NOT EXISTS authorization_snapshot JSONB""",
    """ALTER TABLE collection_runs
    ADD COLUMN IF NOT EXISTS budget_decision_id UUID""",
    """ALTER TABLE collection_runs
    ADD COLUMN IF NOT EXISTS budget_decision_status budget_decision_status""",
    """ALTER TABLE collection_runs
    ADD COLUMN IF NOT EXISTS reviewed_page_cap INTEGER""",
    """ALTER TABLE collection_runs
    ADD COLUMN IF NOT EXISTS reviewed_post_cap INTEGER""",
    """ALTER TABLE collection_runs
    ADD COLUMN IF NOT EXISTS skip_authorization_decision_id UUID""",
    """ALTER TABLE collection_runs
    ADD COLUMN IF NOT EXISTS skip_budget_decision_id UUID""",
    """ALTER TABLE budget_decisions ADD COLUMN IF NOT EXISTS policy_version
    VARCHAR(80) NOT NULL DEFAULT 'legacy'""",
    """ALTER TABLE budget_decisions ADD COLUMN IF NOT EXISTS reviewed_page_cap
    INTEGER NOT NULL DEFAULT 0""",
    """ALTER TABLE budget_decisions ADD COLUMN IF NOT EXISTS reviewed_post_cap
    INTEGER NOT NULL DEFAULT 0""",
    """ALTER TABLE budget_decisions ADD COLUMN IF NOT EXISTS evidence_location
    TEXT NOT NULL DEFAULT 'urn:monitor:legacy-budget-decision'""",
)

_ADD_FOREIGN_KEYS = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_runs_claim_authorization'
    ) THEN
        ALTER TABLE collection_runs ADD CONSTRAINT fk_runs_claim_authorization
        FOREIGN KEY (authorization_decision_id)
        REFERENCES source_authorization_decisions (id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_runs_claim_budget'
    ) THEN
        ALTER TABLE collection_runs ADD CONSTRAINT fk_runs_claim_budget
        FOREIGN KEY (budget_decision_id)
        REFERENCES budget_decisions (id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_runs_skip_authorization'
    ) THEN
        ALTER TABLE collection_runs ADD CONSTRAINT fk_runs_skip_authorization
        FOREIGN KEY (skip_authorization_decision_id)
        REFERENCES source_authorization_decisions (id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_runs_skip_budget'
    ) THEN
        ALTER TABLE collection_runs ADD CONSTRAINT fk_runs_skip_budget
        FOREIGN KEY (skip_budget_decision_id)
        REFERENCES budget_decisions (id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_collection_runs_single_skip_proof'
    ) THEN
        ALTER TABLE collection_runs
        ADD CONSTRAINT ck_collection_runs_single_skip_proof CHECK (
            num_nonnulls(
                skip_authorization_decision_id, skip_budget_decision_id
            ) <= 1
        );
    END IF;
END
$$
"""

_DROP_COLUMNS = (
    "ALTER TABLE collection_runs DROP COLUMN IF EXISTS skip_budget_decision_id",
    "ALTER TABLE collection_runs DROP COLUMN IF EXISTS skip_authorization_decision_id",
    "ALTER TABLE collection_runs DROP COLUMN IF EXISTS reviewed_post_cap",
    "ALTER TABLE collection_runs DROP COLUMN IF EXISTS reviewed_page_cap",
    "ALTER TABLE collection_runs DROP COLUMN IF EXISTS budget_decision_status",
    "ALTER TABLE collection_runs DROP COLUMN IF EXISTS budget_decision_id",
    "ALTER TABLE collection_runs DROP COLUMN IF EXISTS authorization_snapshot",
    "ALTER TABLE collection_runs DROP COLUMN IF EXISTS authorization_decision_id",
    "ALTER TABLE budget_decisions DROP COLUMN IF EXISTS evidence_location",
    "ALTER TABLE budget_decisions DROP COLUMN IF EXISTS reviewed_post_cap",
    "ALTER TABLE budget_decisions DROP COLUMN IF EXISTS reviewed_page_cap",
    "ALTER TABLE budget_decisions DROP COLUMN IF EXISTS policy_version",
)


def upgrade() -> None:
    """Add server-owned claim snapshots and idempotent skip receipts."""
    for statement in _ADD_COLUMNS:
        op.execute(statement)
    op.execute(_ADD_FOREIGN_KEYS)
    op.execute(CreateTable(TABLE, if_not_exists=True))


def downgrade() -> None:
    """Remove Phase 2 claim and skip persistence additions."""
    op.drop_table("collection_skip_observations", if_exists=True)
    for statement in _DROP_COLUMNS:
        op.execute(statement)
