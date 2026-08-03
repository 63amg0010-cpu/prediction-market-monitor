from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[2]
REVISION = "20260803_0012"


def test_0012_is_the_single_head_after_manifold_preparation() -> None:
    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    revision = script.get_revision(REVISION)

    assert revision is not None
    assert revision.down_revision == "20260727_0011"
    assert script.get_heads() == [REVISION]


def test_0012_repairs_only_the_reviewed_scope_and_zeroed_budget() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(config, "20260727_0011:20260803_0012", sql=True)

    sql = output.getvalue()
    assert "phase1-reviewed-v1+manifold-v1" in sql
    assert "soft_stop_units = 70" in sql
    assert "hard_stop_units = 80" in sql
    assert "budget.observed_units = 0" in sql
    assert "budget.paid_spend_enabled = false" in sql
    assert "release-gate:no-spend" in sql
