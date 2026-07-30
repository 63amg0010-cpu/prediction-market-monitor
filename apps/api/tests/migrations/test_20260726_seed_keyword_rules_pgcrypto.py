from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

API_ROOT = Path(__file__).parents[2]
ALEMBIC_INI = API_ROOT / "alembic.ini"
REVISION_RANGE = "20260726_0008:20260726_0009"


def _offline_upgrade() -> str:
    output = StringIO()
    config = Config(
        str(ALEMBIC_INI),
        stdout=output,
        output_buffer=output,
    )
    command.upgrade(config, REVISION_RANGE, sql=True)
    return output.getvalue()


def test_0009_ensures_pgcrypto_before_using_digest() -> None:
    ddl = _offline_upgrade()

    extension = "CREATE EXTENSION IF NOT EXISTS pgcrypto"
    digest_use = "digest("

    assert ddl.count(extension) == 1
    assert ddl.index(extension) < ddl.index(digest_use)


def test_0009_keeps_extension_setup_in_the_data_transaction() -> None:
    ddl = _offline_upgrade()
    extension_offset = ddl.index("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    digest_offset = ddl.index("digest(")

    assert "COMMIT;" not in ddl[extension_offset:digest_offset]


def test_0009_does_not_drop_a_potentially_shared_extension() -> None:
    output = StringIO()
    config = Config(
        str(ALEMBIC_INI),
        stdout=output,
        output_buffer=output,
    )

    command.downgrade(config, "20260726_0009:20260726_0008", sql=True)

    assert "DROP EXTENSION" not in output.getvalue()
