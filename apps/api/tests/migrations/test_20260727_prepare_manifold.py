from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[2]
REVISION = "20260727_0011"
MIGRATION_STATE = API_ROOT / "scripts" / "activation_migration_state.py"


def test_0011_follows_0010e_without_branching() -> None:
    # Given: the committed 0010 compatibility boundary.
    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))

    # When: the activation-preparation revision is resolved.
    revision = script.get_revision(REVISION)

    # Then: 0011 is the sole head and preserves the 0010 ledger schema.
    assert revision is not None
    assert revision.down_revision == "20260803_0010e"
    assert script.get_heads() == [REVISION]


def test_0011_renders_append_only_activation_schema_and_inert_source() -> None:
    # Given: offline rendering cannot consume protected activation evidence.
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    # When: Alembic renders only the 0011 schema boundary.
    command.upgrade(config, "20260803_0010e:20260727_0011", sql=True)
    ddl = output.getvalue()

    # Then: durable evidence/state tables and fail-closed source pointers exist.
    assert "source_activation_attestations" in ddl
    assert "source_binding_change_intents" in ddl
    assert "source_cadence_epochs" in ddl
    assert "source_activation_state_transitions" in ddl
    assert "current_budget_id" in ddl
    assert "current_binding_id" in ddl
    assert "current_cadence_id" in ddl
    assert "platform <> 'manifold' OR NOT enabled OR (" in ddl
    assert "closed_at IS NULL OR closed_at >= created_at_db" in ddl
    assert "DROP CONSTRAINT IF EXISTS enabled_requires_activation_pointers" in ddl
    migration_source = MIGRATION_STATE.read_text(encoding="utf-8")
    assert "'manifold-comments'" in migration_source
    assert "'us', 'manifold', 'manifold-comments'" in migration_source
    assert "false, NULL, NULL, NULL, NULL" in migration_source


def test_0011_offline_downgrade_is_inert_and_retains_evidence() -> None:
    # Given: 0011 append-only evidence may already contain retained content.
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    # When: an ordinary technical downgrade is rendered.
    command.downgrade(config, "20260727_0011:20260803_0010e", sql=True)
    ddl = output.getvalue()

    # Then: the source is disabled and unlinked without dropping evidence tables.
    assert "SET enabled = false" in ddl
    assert "active_authorization_id = NULL" in ddl
    assert "current_budget_id = NULL" in ddl
    assert "current_binding_id = NULL" in ddl
    assert "current_cadence_id = NULL" in ddl
    assert "DROP TABLE source_activation_attestations" not in ddl
    assert "DROP TABLE source_activation_state_transitions" not in ddl
