from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[2]
REVISION = "20260803_0012"


def test_0012_precedes_the_authorization_scope_repair() -> None:
    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    revision = script.get_revision(REVISION)

    assert revision is not None
    assert revision.down_revision == "20260727_0011"
    assert script.get_heads() == ["20260803_0013"]


def test_0012_repairs_only_the_reviewed_scope_and_zeroed_budget() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(config, "20260727_0011:20260803_0012", sql=True)

    sql = output.getvalue()
    assert "phase1-reviewed-v1+manifold-v1" in sql
    assert "INSERT INTO provider_budget_records" in sql
    assert "0, 70, 80, false" in sql
    assert "UPDATE provider_budget_records" not in sql
    assert "SET current_budget_id = repaired_budget.id" in sql
    assert "INSERT INTO source_activation_state_transitions" in sql
    assert "latest_transition.current_budget_id <> old_budget.id" in sql
    assert "free-tier-70-80-v1-repair" in sql
