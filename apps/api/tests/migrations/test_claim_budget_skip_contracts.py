from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config

API_ROOT = Path(__file__).parents[2]
ALEMBIC_INI = API_ROOT / "alembic.ini"


def _phase_two_ddl() -> str:
    output = StringIO()
    config = Config(
        str(ALEMBIC_INI),
        stdout=output,
        output_buffer=output,
    )
    command.upgrade(config, "20260722_0003:20260722_0004", sql=True)
    return output.getvalue()


def test_phase_two_migration_persists_every_run_policy_and_skip_column() -> None:
    # Given: the production migration from the verification head to Phase 2 policy.
    ddl = _phase_two_ddl()

    # When: its offline PostgreSQL contract is inspected.
    required_columns = (
        "authorization_decision_id",
        "authorization_snapshot",
        "budget_decision_id",
        "budget_decision_status",
        "reviewed_page_cap",
        "reviewed_post_cap",
        "skip_authorization_decision_id",
        "skip_budget_decision_id",
    )

    # Then: ORM reload/finalization has durable storage for every field.
    for column in required_columns:
        assert f"ADD COLUMN IF NOT EXISTS {column}" in ddl
    assert "fk_runs_skip_authorization" in ddl
    assert "fk_runs_skip_budget" in ddl
    assert "ck_collection_runs_single_skip_proof" in ddl


def test_skip_observation_migration_contains_full_audit_and_idempotency_fields() -> (
    None
):
    # Given: the same Phase 2 offline DDL.
    ddl = _phase_two_ddl()

    # When: the durable observation receipt is isolated.
    table = ddl.split("CREATE TABLE IF NOT EXISTS collection_skip_observations", 1)[1]
    table = table.split("UPDATE alembic_version", 1)[0]

    # Then: identity, ownership, evidence, decision, and stored response are audited.
    for column in (
        "run_id",
        "command_id",
        "attempt",
        "idempotency_key",
        "request_hash",
        "actor_principal_id",
        "provider",
        "route",
        "http_status",
        "failure_code",
        "decision_kind",
        "decision_id",
        "evidence_sha256",
        "evidence_location",
        "stored_response",
        "created_at",
    ):
        assert column in table
    assert "uq_skip_run_key" in table
    assert "skip_observation_status_code_pair" in table
