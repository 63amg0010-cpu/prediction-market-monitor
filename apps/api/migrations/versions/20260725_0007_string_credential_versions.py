"""Preserve symbolic credential versions in durable principal state."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision: str = "20260725_0007"
down_revision: str | None = "20260724_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "principal_credential_versions"
_COLUMN = "version"
_VERSION_LENGTH = 128
_POSITIVE_CONSTRAINT = "ck_principal_credential_versions_positive_version"
_NONBLANK_CONSTRAINT = (
    "ck_principal_credential_versions_credential_version_nonblank"
)
_ConstraintSemantic = Literal["positive", "nonblank"]


@dataclass(frozen=True)
class CatalogCheckConstraint:
    """A table-bound CHECK constraint read from the database catalog."""

    name: str
    expression: str
    columns: frozenset[str]


class AmbiguousVersionConstraintError(RuntimeError):
    """Raised when multiple CHECK constraints match the migration contract."""


class UnsupportedVersionColumnError(RuntimeError):
    """Raised when the durable version column has an unknown representation."""


def _normalized_expression(expression: str) -> str:
    normalized = re.sub(r'[\s()"]+', "", expression.casefold())
    for cast_name in ("::smallint", "::integer", "::bigint", "::text"):
        normalized = normalized.replace(cast_name, "")
    return normalized


def _matches_semantic(
    constraint: CatalogCheckConstraint,
    semantic: _ConstraintSemantic,
) -> bool:
    if constraint.columns != frozenset({_COLUMN}):
        return False
    expression = _normalized_expression(constraint.expression)
    if semantic == "positive":
        return expression == "version>0"
    return expression in {
        "char_lengthversionbetween1and128",
        "char_lengthversion>=1andchar_lengthversion<=128",
    }


def resolve_version_check_constraint(
    constraints: Sequence[CatalogCheckConstraint],
    *,
    semantic: _ConstraintSemantic,
) -> str | None:
    """Return the exact catalog name for one semantically bound CHECK."""
    matches = tuple(
        constraint.name
        for constraint in constraints
        if _matches_semantic(constraint, semantic)
    )
    if len(matches) > 1:
        joined_names = ", ".join(sorted(matches))
        message = (
            f"ambiguous {semantic} CHECK constraints on {_TABLE}.{_COLUMN}: "
            f"{joined_names}"
        )
        raise AmbiguousVersionConstraintError(message)
    return matches[0] if matches else None


def _catalog_check_constraints(
    bind: Connection,
) -> tuple[CatalogCheckConstraint, ...]:
    if bind.dialect.name != "postgresql":
        inspector = sa.inspect(bind)
        reflected_constraints = inspector.get_check_constraints(_TABLE)
        return tuple(
            CatalogCheckConstraint(
                name=str(item["name"]),
                expression=str(item["sqltext"]),
                columns=(
                    frozenset({_COLUMN})
                    if re.search(rf"\b{_COLUMN}\b", str(item["sqltext"]))
                    else frozenset()
                ),
            )
            for item in reflected_constraints
            if item.get("name") is not None
        )

    statement = sa.text(
        """
        SELECT
            constraint_record.conname AS name,
            pg_get_expr(
                constraint_record.conbin,
                constraint_record.conrelid,
                true
            ) AS expression,
            ARRAY(
                SELECT attribute.attname
                FROM unnest(constraint_record.conkey) WITH ORDINALITY
                    AS key_column(attnum, position)
                JOIN pg_attribute AS attribute
                    ON attribute.attrelid = constraint_record.conrelid
                    AND attribute.attnum = key_column.attnum
                ORDER BY key_column.position
            ) AS columns
        FROM pg_constraint AS constraint_record
        JOIN pg_class AS table_record
            ON table_record.oid = constraint_record.conrelid
        JOIN pg_namespace AS schema_record
            ON schema_record.oid = table_record.relnamespace
        WHERE constraint_record.contype = 'c'
          AND schema_record.nspname = current_schema()
          AND table_record.relname = :table_name
        ORDER BY constraint_record.conname
        """
    )
    rows = bind.execute(statement, {"table_name": _TABLE}).mappings()
    catalog_constraints: list[CatalogCheckConstraint] = []
    for row in rows:
        name = cast("str", row["name"])
        expression = cast("str", row["expression"])
        columns = cast("Sequence[str]", row["columns"])
        catalog_constraints.append(
            CatalogCheckConstraint(
                name=name,
                expression=expression,
                columns=frozenset(columns),
            )
        )
    return tuple(catalog_constraints)


def _version_column_type(bind: Connection) -> sa.types.TypeEngine[object]:
    inspector = sa.inspect(bind)
    matching_columns = tuple(
        column
        for column in inspector.get_columns(_TABLE)
        if column["name"] == _COLUMN
    )
    if len(matching_columns) != 1:
        message = f"expected exactly one {_TABLE}.{_COLUMN} column"
        raise UnsupportedVersionColumnError(message)
    return cast("sa.types.TypeEngine[object]", matching_columns[0]["type"])


def _drop_semantic_constraint(
    constraints: Sequence[CatalogCheckConstraint],
    *,
    semantic: _ConstraintSemantic,
) -> bool:
    constraint_name = resolve_version_check_constraint(
        constraints,
        semantic=semantic,
    )
    if constraint_name is None:
        return False
    op.drop_constraint(op.f(constraint_name), _TABLE, type_="check")
    return True


def _create_constraint(
    *,
    name: str,
    expression: str,
) -> None:
    op.create_check_constraint(op.f(name), _TABLE, expression)


def _alter_to_string() -> None:
    op.alter_column(
        _TABLE,
        _COLUMN,
        existing_type=sa.Integer(),
        type_=sa.String(length=_VERSION_LENGTH),
        existing_nullable=False,
        postgresql_using="version::text",
    )


def _alter_to_integer() -> None:
    op.alter_column(
        _TABLE,
        _COLUMN,
        existing_type=sa.String(length=_VERSION_LENGTH),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="version::integer",
    )


def _offline_upgrade() -> None:
    op.drop_constraint(op.f(_POSITIVE_CONSTRAINT), _TABLE, type_="check")
    _alter_to_string()
    _create_constraint(
        name=_NONBLANK_CONSTRAINT,
        expression="char_length(version) BETWEEN 1 AND 128",
    )


def _offline_downgrade() -> None:
    op.drop_constraint(op.f(_NONBLANK_CONSTRAINT), _TABLE, type_="check")
    _alter_to_integer()
    _create_constraint(
        name=_POSITIVE_CONSTRAINT,
        expression="version > 0",
    )


def upgrade() -> None:
    """Store the exact symbolic version carried by service-token claims."""
    if op.get_context().as_sql:
        _offline_upgrade()
        return

    bind = op.get_bind()
    column_type = _version_column_type(bind)
    if isinstance(column_type, sa.Integer):
        _ = _drop_semantic_constraint(
            _catalog_check_constraints(bind),
            semantic="positive",
        )
        _alter_to_string()
    elif not (
        isinstance(column_type, sa.String)
        and column_type.length == _VERSION_LENGTH
    ):
        message = f"unsupported {_TABLE}.{_COLUMN} type: {column_type!s}"
        raise UnsupportedVersionColumnError(message)

    constraints = _catalog_check_constraints(bind)
    if (
        resolve_version_check_constraint(constraints, semantic="nonblank")
        is None
    ):
        _create_constraint(
            name=_NONBLANK_CONSTRAINT,
            expression="char_length(version) BETWEEN 1 AND 128",
        )


def downgrade() -> None:
    """Restore the integer-only representation when all values are numeric."""
    if op.get_context().as_sql:
        _offline_downgrade()
        return

    bind = op.get_bind()
    column_type = _version_column_type(bind)
    if (
        isinstance(column_type, sa.String)
        and column_type.length == _VERSION_LENGTH
    ):
        _ = _drop_semantic_constraint(
            _catalog_check_constraints(bind),
            semantic="nonblank",
        )
        _alter_to_integer()
    elif not isinstance(column_type, sa.Integer):
        message = f"unsupported {_TABLE}.{_COLUMN} type: {column_type!s}"
        raise UnsupportedVersionColumnError(message)

    constraints = _catalog_check_constraints(bind)
    if (
        resolve_version_check_constraint(constraints, semantic="positive")
        is None
    ):
        _create_constraint(
            name=_POSITIVE_CONSTRAINT,
            expression="version > 0",
        )
