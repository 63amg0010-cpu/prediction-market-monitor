"""Defer the cyclic latest-report pointer until transaction commit."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0005"
down_revision: str | None = "20260722_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROP_CONSTRAINT = """
ALTER TABLE daily_reports
DROP CONSTRAINT IF EXISTS fk_daily_reports_latest_version
"""

_ADD_DEFERRED_CONSTRAINT = """
ALTER TABLE daily_reports
ADD CONSTRAINT fk_daily_reports_latest_version
FOREIGN KEY (latest_version_id)
REFERENCES daily_report_versions (id)
ON DELETE RESTRICT
DEFERRABLE INITIALLY DEFERRED
"""

_ADD_IMMEDIATE_CONSTRAINT = """
ALTER TABLE daily_reports
ADD CONSTRAINT fk_daily_reports_latest_version
FOREIGN KEY (latest_version_id)
REFERENCES daily_report_versions (id)
ON DELETE RESTRICT
NOT DEFERRABLE
"""


def upgrade() -> None:
    """Permit one transaction to create a report and its latest version."""
    op.execute(_DROP_CONSTRAINT)
    op.execute(_ADD_DEFERRED_CONSTRAINT)


def downgrade() -> None:
    """Restore the original immediate pointer constraint."""
    op.execute(_DROP_CONSTRAINT)
    op.execute(_ADD_IMMEDIATE_CONSTRAINT)
