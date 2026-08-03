from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[2]
REVISION = "20260803_0013"


def test_0013_is_the_single_head_after_policy_repair() -> None:
    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    revision = script.get_revision(REVISION)

    assert revision is not None
    assert revision.down_revision == "20260803_0012"
    assert script.get_heads() == [REVISION]


def test_0013_appends_exact_reviewed_scope_and_moves_only_pointers() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(config, "20260803_0012:20260803_0013", sql=True)

    sql = output.getvalue()
    for field in (
        "market.id",
        "market.question",
        "market.market_slug",
        "market.neutral_url",
        "comment.id",
        "comment.contractId",
        "comment.createdTime",
        "comment.content.text",
    ):
        assert field in sql
    assert "INSERT INTO source_authorization_decisions" in sql
    assert "UPDATE source_authorization_decisions" not in sql
    assert "DELETE FROM source_authorization_decisions" not in sql
    assert "SET active_authorization_id = repaired_authorization.id" in sql
    assert "INSERT INTO source_activation_state_transitions" in sql
    assert "latest_transition.current_authorization_id" in sql
