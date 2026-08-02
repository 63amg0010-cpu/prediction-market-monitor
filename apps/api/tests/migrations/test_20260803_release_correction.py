from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

API_ROOT = Path(__file__).parents[2]


def test_0010a_is_the_inert_release_foundation_boundary() -> None:
    config = Config(str(API_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    correction = script.get_revision("20260803_0010a")
    head = script.get_revision("20260727_0011")

    assert correction is not None
    assert correction.down_revision == "20260727_0010"
    assert head is not None
    assert head.down_revision == "20260803_0010a"


def test_0010a_renders_generic_receipts_without_manifold_source() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(config, "20260727_0010:20260803_0010a", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE release_receipt_chain" in sql
    assert "ADD COLUMN repository" in sql
    assert "ADD COLUMN claimed_run_attempt" in sql
    assert "ENABLE ROW LEVEL SECURITY" in sql
    assert "manifold-comments" not in sql
    assert "source_activation_attestations" not in sql


def test_0010a_offline_downgrade_removes_only_generic_foundation() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    command.downgrade(config, "20260803_0010a:20260727_0010", sql=True)

    sql = output.getvalue()
    assert "DROP TABLE release_receipt_chain" in sql
    assert "DROP COLUMN repository" in sql
    assert "DROP TABLE community_sources" not in sql
