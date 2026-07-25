"""Preserve symbolic credential versions in durable principal state."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260725_0007"
down_revision: str | None = "20260724_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "principal_credential_versions"
_POSITIVE_CONSTRAINT = "ck_principal_credential_versions_positive_version"
_NONBLANK_CONSTRAINT = (
    "ck_principal_credential_versions_credential_version_nonblank"
)


def upgrade() -> None:
    """Store the exact symbolic version carried by service-token claims."""
    op.drop_constraint(_POSITIVE_CONSTRAINT, _TABLE, type_="check")
    op.alter_column(
        _TABLE,
        "version",
        existing_type=sa.Integer(),
        type_=sa.String(length=128),
        existing_nullable=False,
        postgresql_using="version::text",
    )
    op.create_check_constraint(
        _NONBLANK_CONSTRAINT,
        _TABLE,
        "char_length(version) BETWEEN 1 AND 128",
    )


def downgrade() -> None:
    """Restore the integer-only representation when all values are numeric."""
    op.drop_constraint(_NONBLANK_CONSTRAINT, _TABLE, type_="check")
    op.alter_column(
        _TABLE,
        "version",
        existing_type=sa.String(length=128),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="version::integer",
    )
    op.create_check_constraint(
        _POSITIVE_CONSTRAINT,
        _TABLE,
        "version > 0",
    )
