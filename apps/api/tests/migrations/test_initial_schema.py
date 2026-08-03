from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[2]
ALEMBIC_INI = API_ROOT / "alembic.ini"
EXPECTED_HEAD = "20260727_0011"


def _alembic_config() -> tuple[Config, StringIO]:
    output = StringIO()
    config = Config(
        str(ALEMBIC_INI),
        stdout=output,
        output_buffer=output,
    )
    return config, output


def test_phase_two_contract_migration_is_the_only_current_head() -> None:
    # Given: the API Alembic configuration.
    config, _ = _alembic_config()

    # When: the revision graph is loaded from disk.
    script = ScriptDirectory.from_config(config)
    revisions = tuple(script.walk_revisions())

    # Then: every Phase 2 correction remains on one ordered migration chain.
    assert script.get_heads() == [EXPECTED_HEAD]
    assert tuple(item.revision for item in revisions) == (
        "20260727_0011",
        "20260803_0010c",
        "20260803_0010b",
        "20260803_0010a",
        "20260727_0010",
        "20260726_0009",
        "20260726_0008",
        "20260725_0007",
        "20260724_0006",
        "20260723_0005",
        "20260722_0004",
        "20260722_0003",
        "20260722_0002",
        "20260721_0001",
    )
    assert tuple(item.down_revision for item in revisions) == (
        "20260803_0010c",
        "20260803_0010b",
        "20260803_0010a",
        "20260727_0010",
        "20260726_0009",
        "20260726_0008",
        "20260725_0007",
        "20260724_0006",
        "20260723_0005",
        "20260722_0004",
        "20260722_0003",
        "20260722_0002",
        "20260721_0001",
        None,
    )


def test_upgrade_head_generates_postgresql_schema_and_security_triggers() -> None:
    # Given: a real Alembic offline PostgreSQL output buffer.
    config, output = _alembic_config()

    # When: upgrade-to-head SQL is generated without a database connection.
    command.upgrade(config, "head", sql=True)
    ddl = output.getvalue()

    # Then: critical durable tables and fail-closed triggers are present.
    assert "CREATE TABLE community_sources" in ddl
    assert "CREATE TABLE page_commits" in ddl
    assert "CREATE TABLE report_input_manifests" in ddl
    assert "terminal_page_commit_id UUID" in ddl
    assert "compressed_payload BYTEA NOT NULL" in ddl
    assert "report_payload BYTEA NOT NULL" in ddl
    assert "ALTER COLUMN version TYPE VARCHAR(128)" in ddl
    assert "DCInside 예측마켓 미니 갤러리" in ddl
    assert "phase1-reviewed-v1" in ddl
    assert "free-tier-70-80-v1" in ddl
    assert "e72c1ebdae3f7318a76dfee09408730ab52169cad1a2dbc65ac24d277eca1a8d" in ddl
    assert "prediction market regulation" in ddl
    assert (
        "DROP CONSTRAINT IF EXISTS report_input_tombstones_first_manifest_id_fkey"
    ) in ddl
    assert "CREATE TRIGGER trg_community_sources_authorization" in ddl
    assert "CREATE TRIGGER trg_source_authorization_append_only" in ddl
    assert "CREATE TRIGGER trg_post_versions_immutable" in ddl
    assert "CREATE TRIGGER trg_page_commits_immutable" in ddl
    assert EXPECTED_HEAD in ddl


def test_downgrade_head_to_base_generates_complete_drop_sql() -> None:
    # Given: the same revision graph used for upgrade generation.
    config, output = _alembic_config()

    # When: a head-to-base offline downgrade is generated.
    command.downgrade(config, "head:base", sql=True)
    ddl = output.getvalue()

    # Then: schema tables and supporting trigger functions are removed.
    assert "DROP TABLE community_sources" in ddl
    assert "DROP TABLE report_input_manifests" in ddl
    assert "DROP FUNCTION IF EXISTS monitor_require_active_source_authorization" in ddl
