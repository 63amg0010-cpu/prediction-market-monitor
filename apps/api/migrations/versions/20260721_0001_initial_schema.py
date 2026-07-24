"""Create the Phase 1 durable PostgreSQL control-plane schema."""

from collections.abc import Sequence

from alembic import op
from app.db.models import metadata
from app.db.triggers import (
    DROP_FUNCTION_DDL,
    DROP_TRIGGER_DDL,
    FUNCTION_DDL,
    TRIGGER_DDL,
)

revision: str = "20260721_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create typed tables before installing fail-closed triggers."""
    metadata.create_all(bind=op.get_bind(), checkfirst=False)
    for statement in (*FUNCTION_DDL, *TRIGGER_DDL):
        op.execute(statement)


def downgrade() -> None:
    """Remove triggers, functions, and schema objects in dependency order."""
    for statement in (*DROP_TRIGGER_DDL, *DROP_FUNCTION_DDL):
        op.execute(statement)
    metadata.drop_all(bind=op.get_bind(), checkfirst=False)
