from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

API_ROOT = Path(__file__).parents[2]


def test_0011_adds_generic_append_only_release_reservations() -> None:
    output = StringIO()
    config = Config(str(API_ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(config, "20260727_0010:20260727_0011", sql=True)
    ddl = output.getvalue()

    assert "release_receipt_chain" in ddl
    assert "approval_launch_sha256s" in ddl
    assert "claimed_run_attempt" in ddl
    assert "DROP CONSTRAINT release_reservation_operation" in ddl
    assert "DROP CONSTRAINT release_reservation_revision" in ddl
    assert "attempt >= 1" in ddl
    assert "WHERE claimed_run_id IS NOT NULL" in ddl
