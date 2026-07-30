import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from io import StringIO
from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.db.base import NAMING_CONVENTION
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable
from tests.migrations import string_credential_versions_fakes as fakes

API_ROOT = Path(__file__).parents[2]
MIGRATION_PATH = (
    API_ROOT
    / "migrations"
    / "versions"
    / "20260725_0007_string_credential_versions.py"
)
MIGRATION_SPEC = spec_from_file_location(
    "string_credential_versions_migration",
    MIGRATION_PATH,
)
assert MIGRATION_SPEC is not None
assert MIGRATION_SPEC.loader is not None
RAW_MIGRATION = module_from_spec(MIGRATION_SPEC)
sys.modules[MIGRATION_SPEC.name] = RAW_MIGRATION
MIGRATION_SPEC.loader.exec_module(RAW_MIGRATION)
MIGRATION = cast("fakes.MigrationModule", cast("object", RAW_MIGRATION))


def _offline_sql(direction: str) -> str:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)
    if direction == "upgrade":
        command.upgrade(config, "20260724_0006:20260725_0007", sql=True)
    else:
        command.downgrade(config, "20260725_0007:20260724_0006", sql=True)
    return output.getvalue()


def _postgresql_truncated_constraint_name() -> tuple[str, str]:
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)
    logical_name = "ck_principal_credential_versions_positive_version"
    table = sa.Table(
        "principal_credential_versions",
        metadata,
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("version > 0", name=logical_name),
    )
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    match = re.search(r"CONSTRAINT (\S+) CHECK", ddl)
    assert match is not None
    return logical_name, match.group(1)


def test_postgresql_truncation_is_resolved_by_catalog_semantics() -> None:
    logical_name, actual_name = _postgresql_truncated_constraint_name()
    candidate = MIGRATION.CatalogCheckConstraint(
        name=actual_name,
        expression="version > 0",
        columns=frozenset({"version"}),
    )

    resolved = MIGRATION.resolve_version_check_constraint(
        (candidate,),
        semantic="positive",
    )

    assert len(logical_name) <= postgresql.dialect().max_identifier_length
    assert actual_name != logical_name
    assert len(actual_name) <= postgresql.dialect().max_identifier_length
    assert resolved == actual_name


def test_semantic_resolution_refuses_ambiguous_catalog_matches() -> None:
    candidates = (
        MIGRATION.CatalogCheckConstraint(
            name="positive_a",
            expression="version > 0",
            columns=frozenset({"version"}),
        ),
        MIGRATION.CatalogCheckConstraint(
            name="positive_b",
            expression="(version > 0)",
            columns=frozenset({"version"}),
        ),
    )

    with pytest.raises(MIGRATION.AmbiguousVersionConstraintError):
        _ = MIGRATION.resolve_version_check_constraint(
            candidates,
            semantic="positive",
        )


def test_semantic_resolution_requires_exact_version_column_binding() -> None:
    compound_candidate = MIGRATION.CatalogCheckConstraint(
        name="positive_but_compound",
        expression="version > 0",
        columns=frozenset({"version", "principal_id"}),
    )

    resolved = MIGRATION.resolve_version_check_constraint(
        (compound_candidate,),
        semantic="positive",
    )

    assert resolved is None


def test_upgrade_accepts_fresh_schema_already_at_string_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = fakes.OperationRecorder(monkeypatch, MIGRATION)
    recorder.configure_online(
        column_type=sa.String(length=128),
        constraints=(
            MIGRATION.CatalogCheckConstraint(
                name=(
                    "ck_principal_credential_versions_"
                    "credential_version_nonblank"
                ),
                expression=(
                    "char_length(version::text) >= 1 "
                    "AND char_length(version::text) <= 128"
                ),
                columns=frozenset({"version"}),
            ),
        ),
    )

    _ = MIGRATION.upgrade()

    assert recorder.calls == []


def test_upgrade_drops_the_exact_semantically_matched_catalog_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, actual_name = _postgresql_truncated_constraint_name()
    recorder = fakes.OperationRecorder(monkeypatch, MIGRATION)
    recorder.configure_online(
        column_type=sa.Integer(),
        constraints=(
            MIGRATION.CatalogCheckConstraint(
                name=actual_name,
                expression="version > 0",
                columns=frozenset({"version"}),
            ),
        ),
    )

    _ = MIGRATION.upgrade()

    assert recorder.calls[0] == (
        "drop",
        actual_name,
        "principal_credential_versions",
        "check",
    )
    assert [call[0] for call in recorder.calls] == ["drop", "alter", "create"]


def test_downgrade_drops_exact_nonblank_constraint_and_restores_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_name = "custom_catalog_nonblank_name"
    recorder = fakes.OperationRecorder(monkeypatch, MIGRATION)
    recorder.configure_online(
        column_type=sa.String(length=128),
        constraints=(
            MIGRATION.CatalogCheckConstraint(
                name=actual_name,
                expression=(
                    "char_length(version::text) >= 1 "
                    "AND char_length(version::text) <= 128"
                ),
                columns=frozenset({"version"}),
            ),
        ),
    )

    _ = MIGRATION.downgrade()

    assert recorder.calls[0] == (
        "drop",
        actual_name,
        "principal_credential_versions",
        "check",
    )
    assert [call[0] for call in recorder.calls] == ["drop", "alter", "create"]


def test_downgrade_accepts_integer_positive_residue_without_ddl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = fakes.OperationRecorder(monkeypatch, MIGRATION)
    recorder.configure_online(
        column_type=sa.Integer(),
        constraints=(
            MIGRATION.CatalogCheckConstraint(
                name="custom_catalog_positive_name",
                expression="version > 0",
                columns=frozenset({"version"}),
            ),
        ),
    )

    _ = MIGRATION.downgrade()

    assert recorder.calls == []


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
def test_offline_sql_uses_canonical_names_without_double_convention(
    direction: str,
) -> None:
    ddl = _offline_sql(direction)

    assert (
        "ck_principal_credential_versions_ck_principal_credentia_1a16"
        not in ddl
    )
    assert "ck_principal_credential_versions_positive_version" in ddl
    assert (
        "ck_principal_credential_versions_credential_version_nonblank" in ddl
    )
