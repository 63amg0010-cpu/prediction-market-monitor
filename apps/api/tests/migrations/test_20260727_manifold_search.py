from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[2]
REVISION = "20260727_0010"
MIGRATION = API_ROOT / "migrations" / "versions" / (
    "20260727_0010_manifold_search_compatibility.py"
)


def test_0010_follows_the_characterized_0009_baseline() -> None:
    # Given: the previously passing, single-head 0009 migration graph.
    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))

    # When: the compatibility revision is loaded.
    revision = script.get_revision(REVISION)

    # Then: 0010 extends 0009 without branching or rewriting history.
    assert revision is not None
    assert revision.down_revision == "20260726_0009"
    assert script.get_heads() == ["20260803_0012"]


def test_0010_declares_the_exact_search_and_enum_boundary_contract() -> None:
    # Given: the migration module that owns the PostgreSQL compatibility boundary.
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    # When: Alembic renders the exact compatibility upgrade.
    command.upgrade(config, "20260726_0009:20260727_0010", sql=True)
    ddl = output.getvalue()

    # Then: Todo 3's fold, one LF, C collation, and trigram index are exact.
    assert "chr(9)||chr(10)||chr(11)||chr(12)||chr(13)||chr(32)" in ddl
    assert "LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE" in ddl
    assert "E'\\n'" in ddl
    assert (
        "CREATE INDEX ix_post_versions_search_text_trgm ON post_versions "
        'USING gin ((search_text COLLATE "C") gin_trgm_ops)'
    ) in ddl


def test_0010_exposes_stable_downgrade_refusal_code() -> None:
    # Given: operators need a machine-readable refusal before destructive DDL.
    migration_source = MIGRATION.read_text(encoding="utf-8")

    # When/Then: the migration exports the stable incident code.
    assert (
        'DOWNGRADE_DEPENDENCY_CODE: Final = "manifold_downgrade_dependency"'
        in migration_source
    )
