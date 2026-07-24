"""Keep the first manifest identifier as immutable deletion provenance."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260722_0002"
down_revision: str | None = "20260721_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DROP_FIRST_MANIFEST_FK = """
ALTER TABLE report_input_tombstones
DROP CONSTRAINT IF EXISTS report_input_tombstones_first_manifest_id_fkey
"""

_RESTORE_FIRST_MANIFEST_FK = """
ALTER TABLE report_input_tombstones
ADD CONSTRAINT report_input_tombstones_first_manifest_id_fkey
FOREIGN KEY (first_manifest_id)
REFERENCES report_input_manifests (id)
ON DELETE RESTRICT
"""


def upgrade() -> None:
    """Allow tombstones to outlive the first manifest that referenced them."""
    op.execute(_DROP_FIRST_MANIFEST_FK)


def downgrade() -> None:
    """Restore the original restrictive ownership relation."""
    op.execute(_RESTORE_FIRST_MANIFEST_FK)
