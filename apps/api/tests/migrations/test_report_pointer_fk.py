from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db.models import metadata
from sqlalchemy import ForeignKeyConstraint

API_ROOT = Path(__file__).parents[2]
ALEMBIC_INI = API_ROOT / "alembic.ini"
EXPECTED_HEAD = "20260726_0008"


def _alembic_config() -> tuple[Config, StringIO]:
    output = StringIO()
    config = Config(str(ALEMBIC_INI), stdout=output, output_buffer=output)
    return config, output


def test_latest_report_pointer_fk_is_deferred_until_commit() -> None:
    # Given: the ORM constraint used by first-version report appends.
    table = metadata.tables["daily_reports"]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, ForeignKeyConstraint)
        and item.name == "fk_daily_reports_latest_version"
    )

    # When/Then: the report pointer may target its version within one transaction.
    assert constraint.deferrable is True
    assert constraint.initially == "DEFERRED"


def test_report_pointer_deferral_migration_upgrades_and_downgrades() -> None:
    # Given: the complete PostgreSQL migration graph.
    config, upgrade_output = _alembic_config()
    scripts = ScriptDirectory.from_config(config)

    # When: head is rendered in both directions.
    command.upgrade(config, "head", sql=True)
    upgrade_sql = upgrade_output.getvalue()
    downgrade_config, downgrade_output = _alembic_config()
    command.downgrade(
        downgrade_config,
        f"{EXPECTED_HEAD}:20260722_0004",
        sql=True,
    )
    downgrade_sql = downgrade_output.getvalue()

    # Then: existing databases gain the deferred FK and rollback restores immediacy.
    assert scripts.get_heads() == [EXPECTED_HEAD]
    assert "DEFERRABLE INITIALLY DEFERRED" in upgrade_sql
    assert "NOT DEFERRABLE" in downgrade_sql
