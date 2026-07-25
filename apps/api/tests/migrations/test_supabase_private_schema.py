from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db.models import metadata

API_ROOT = Path(__file__).parents[2]
ALEMBIC_INI = API_ROOT / "alembic.ini"
EXPECTED_HEAD = "20260725_0007"


def _alembic_config() -> tuple[Config, StringIO]:
    output = StringIO()
    config = Config(str(ALEMBIC_INI), stdout=output, output_buffer=output)
    return config, output


def test_supabase_head_denies_data_api_access_and_hardens_functions() -> None:
    # Given: the repository migration graph and every public application table.
    config, output = _alembic_config()
    script = ScriptDirectory.from_config(config)

    # When: the Supabase hardening revision is rendered as PostgreSQL SQL.
    command.upgrade(config, "20260723_0005:head", sql=True)
    ddl = output.getvalue()

    # Then: the exposed schema is fail-closed and privileged routines are private.
    assert script.get_heads() == [EXPECTED_HEAD]
    for table_name in (*sorted(metadata.tables), "alembic_version"):
        assert f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY" in ddl
        assert (
            f"REVOKE ALL PRIVILEGES ON TABLE public.{table_name} "
            "FROM PUBLIC, anon, authenticated"
        ) in ddl
    for function_name in (
        "monitor_reject_mutation",
        "monitor_reject_update",
        "monitor_require_active_source_authorization",
        "monitor_require_page_source_authorization",
        "monitor_require_running_source_authorization",
    ):
        assert (
            f"ALTER FUNCTION public.{function_name}() SET search_path = public, pg_temp"
        ) in ddl
        assert (
            f"REVOKE EXECUTE ON FUNCTION public.{function_name}() "
            "FROM PUBLIC, anon, authenticated"
        ) in ddl
    assert ddl.count("CREATE INDEX IF NOT EXISTS ix_") == 40


def test_downgrade_restores_exact_supabase_table_acl_without_public_grants() -> None:
    # Given: the hardening revision applied over Supabase's default table ACL.
    config, output = _alembic_config()

    # When: only the Supabase hardening revision is rolled back.
    command.downgrade(config, f"{EXPECTED_HEAD}:20260723_0005", sql=True)
    ddl = output.getvalue()

    # Then: anon/authenticated regain arwdDxtm and PUBLIC remains unprivileged.
    for table_name in (*sorted(metadata.tables), "alembic_version"):
        grant = (
            f"GRANT ALL PRIVILEGES ON TABLE public.{table_name} TO anon, authenticated"
        )
        disable_rls = f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY"
        assert grant in ddl
        assert disable_rls in ddl
        assert ddl.index(grant) < ddl.index(disable_rls)
        assert f"ON TABLE public.{table_name} TO PUBLIC" not in ddl
    assert ddl.count("DROP INDEX IF EXISTS ix_") == 40
    for function_name in (
        "monitor_reject_mutation",
        "monitor_reject_update",
        "monitor_require_active_source_authorization",
        "monitor_require_page_source_authorization",
        "monitor_require_running_source_authorization",
    ):
        function = f"public.{function_name}()"
        reset = f"ALTER FUNCTION {function} RESET search_path"
        grant = f"GRANT EXECUTE ON FUNCTION {function} TO anon, authenticated"
        assert reset in ddl
        assert grant in ddl
        assert ddl.index(grant) < ddl.index(reset)
        assert f"GRANT EXECUTE ON FUNCTION {function} TO PUBLIC" not in ddl
        function_statements = tuple(
            line for line in ddl.splitlines() if f"FUNCTION {function}" in line
        )
        assert len(function_statements) == 2
        assert all("service_role" not in line for line in function_statements)
    assert ddl.count("GRANT EXECUTE ON FUNCTION public.") == 5
