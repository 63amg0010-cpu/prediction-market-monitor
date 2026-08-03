"""Repair the active multi-source scope and Manifold free-budget policy."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_0012"
down_revision: str | None = "20260727_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Align both reviewed sources and repair only the exact zeroed budget."""
    op.execute(
        """
        UPDATE community_sources
        SET scope_version = 'phase1-reviewed-v1+manifold-v1'
        WHERE id IN (
            'd6dc5ea1-e3af-4bfe-88ad-e4beffd22ab6',
            '0890756a-ca23-5697-ae4c-0de527361064'
        )
          AND scope_version IN (
              'phase1-reviewed-v1',
              'phase1-reviewed-v1+manifold-v1'
          )
        """
    )
    op.execute(
        """
        UPDATE provider_budget_records AS budget
        SET soft_stop_units = 70,
            hard_stop_units = 80
        FROM community_sources AS source
        WHERE source.id = '0890756a-ca23-5697-ae4c-0de527361064'
          AND source.current_budget_id = budget.id
          AND budget.provider = 'manifold'
          AND budget.observed_units = 0
          AND budget.soft_stop_units IN (0, 70)
          AND budget.hard_stop_units IN (0, 80)
          AND budget.paid_spend_enabled = false
          AND budget.evidence_location = 'release-gate:no-spend'
        """
    )


def downgrade() -> None:
    """Retain the safe data correction instead of restoring an invalid policy."""
    op.execute("SELECT 1")
