"""Prepare append-only activation evidence while Manifold remains disabled."""

from collections.abc import Sequence

from alembic import context, op
from scripts.activation_migration_state import (
    append_deactivated_transition,
    prepare_source,
)
from scripts.activation_schema import FOREIGN_KEYS_SQL, SCHEMA_SQL
from scripts.release_cadence_schema import TABLES as CADENCE_TABLES
from scripts.release_cadence_schema import execute_schema

revision: str = "20260727_0011"
down_revision: str | None = "20260803_0010c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create or verify canonical activation objects before preparing evidence."""
    release_table = "public.release_receipt_chain"
    release_roles = "PUBLIC, anon, authenticated"
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {release_table} FROM {release_roles}")
    for statement in SCHEMA_SQL.split(";\n"):
        if statement.strip():
            op.execute(statement)
    op.execute(FOREIGN_KEYS_SQL)
    execute_schema(
        op.get_bind(),
        offline=context.is_offline_mode(),
        alembic_execute=op.execute,
    )
    for table_name in (
        "source_activation_attestations",
        "source_binding_change_intents",
        "source_cadence_epochs",
        "source_activation_state_transitions",
        *CADENCE_TABLES,
    ):
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(
            "".join(
                (
                    f"REVOKE ALL PRIVILEGES ON TABLE public.{table_name} ",
                    "FROM PUBLIC, anon, authenticated",
                )
            )
        )
    if not context.is_offline_mode():
        prepare_source()


def downgrade() -> None:
    """Leave retained evidence inert and unlink every current activation pointer."""
    op.execute(
        """
        UPDATE cadence_epoch_contracts
        SET invalidated_at = COALESCE(invalidated_at, transaction_timestamp())
        WHERE invalidated_at IS NULL
        """
    )
    op.execute(
        """
        UPDATE source_cadence_epochs
        SET closed_at = COALESCE(closed_at, transaction_timestamp())
        WHERE source_id = '0890756a-ca23-5697-ae4c-0de527361064'
          AND closed_at IS NULL
        """
    )
    if not context.is_offline_mode():
        append_deactivated_transition()
    op.execute(
        """
        UPDATE community_sources
        SET enabled = false,
            active_authorization_id = NULL,
            current_budget_id = NULL,
            current_binding_id = NULL,
            current_cadence_id = NULL
        WHERE id = '0890756a-ca23-5697-ae4c-0de527361064'
        """
    )
