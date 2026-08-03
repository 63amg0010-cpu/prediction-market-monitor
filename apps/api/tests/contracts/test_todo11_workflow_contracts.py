from __future__ import annotations

from pathlib import Path

import yaml
from app.domain.types import JsonValue
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW_ADAPTER = TypeAdapter(dict[str, JsonValue])

COMMON_MANUAL_INPUTS = {
    "expected_sha": "string",
    "expected_plan_sha256": "string",
    "activation_nonce": "string",
    "dispatch_nonce": "string",
    "reservation_sha256": "string",
    "attempt": "number",
}
CLAIM_REQUEST_FIELDS = {
    "repository",
    "workflow",
    "display_title",
    "head_sha",
    "approved_plan_sha256",
    "activation_nonce",
    "dispatch_nonce",
    "reservation_sha256",
    "run_id",
    "run_attempt",
    "event",
    "ref",
    "environment",
}


def _workflow(name: str) -> dict[str, JsonValue]:
    source = (WORKFLOWS / name).read_text(encoding="utf-8")
    normalized = source.replace("\non:\n", "\n'on':\n", 1)
    return WORKFLOW_ADAPTER.validate_python(yaml.safe_load(normalized))


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _job(workflow: dict[str, JsonValue], name: str) -> dict[str, JsonValue]:
    return _mapping(_mapping(workflow["jobs"])[name])


def _steps(job: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], ...]:
    values = job["steps"]
    assert isinstance(values, list)
    return tuple(_mapping(value) for value in values if isinstance(value, dict))


def _named_steps(job: dict[str, JsonValue]) -> dict[str, dict[str, JsonValue]]:
    return {str(step["name"]): step for step in _steps(job) if "name" in step}


def _dispatch_inputs(workflow: dict[str, JsonValue]) -> dict[str, JsonValue]:
    triggers = _mapping(workflow["on"])
    dispatch = _mapping(triggers["workflow_dispatch"])
    return _mapping(dispatch["inputs"])


def _assert_common_inputs(workflow: dict[str, JsonValue]) -> None:
    inputs = _dispatch_inputs(workflow)
    for name, input_type in COMMON_MANUAL_INPUTS.items():
        specification = _mapping(inputs[name])
        assert specification["required"] is True
        assert specification["type"] == input_type


def _assert_immutable_checkout(job: dict[str, JsonValue]) -> None:
    checkout = next(
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    options = _mapping(checkout["with"])
    assert options["ref"] == (
        "${{ github.event_name == 'workflow_dispatch' "
        "&& inputs.expected_sha || github.sha }}"
    )
    assert options["persist-credentials"] is False


def _assert_manual_validation(job: dict[str, JsonValue]) -> None:
    command = str(_named_steps(job)["Validate immutable workflow identity"]["run"])
    assert 'test "$GITHUB_RUN_ATTEMPT" = "1"' in command
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in command
    assert 'test "$(git rev-parse HEAD)" = "$GITHUB_EVENT_SHA"' in command
    assert 'test "$GITHUB_EVENT_SHA" = "$EXPECTED_SHA"' in command
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"' in command
    assert 'test "$ACTIVATION_NONCE" != "$DISPATCH_NONCE"' in command
    assert '[ "$ATTEMPT" = "1" ] || [ "$ATTEMPT" = "2" ]' in command
    assert "EXPECTED_PLAN_SHA256" in command
    assert "RESERVATION_SHA256" in command


def _assert_claim_contract(
    job: dict[str, JsonValue],
    *,
    expected_environment: str,
    expected_workflow: str,
    expected_condition: str = "${{ github.event_name == 'workflow_dispatch' }}",
) -> None:
    step = _named_steps(job)["Claim durable workflow reservation"]
    assert step["if"] == expected_condition
    command = str(step["run"])
    assert "/internal/release/workflow-dispatch-claim" in command
    assert "audience=monitor-control" in command
    assert "--retry" not in command
    assert "sleep " not in command
    assert '"schema","command","reviewed_sha","approved_plan_sha256"' in command
    assert '"reservation_sha256","run_id","run_attempt","event","ref"' in command
    assert '.schema == "release-chain-receipt.v1"' in command
    assert '.command == "workflow-dispatch-claim"' in command
    assert ".accepted == true" in command
    assert ".terminal_for_attempt == false" in command
    assert ".retry_permitted == false" in command
    assert ".predecessor_receipt_sha256 == $reservation_sha256" in command
    assert '.database_timestamps | (keys | sort) == ["claimed_at_db"' in command
    for field in CLAIM_REQUEST_FIELDS:
        assert f"--arg {field} " in command or f"{field}:" in command
    environment = _mapping(step["env"])
    assert environment["CLAIM_ENVIRONMENT"] == expected_environment
    assert environment["WORKFLOW_FILE"] == expected_workflow


def _assert_attempt_artifact(
    job: dict[str, JsonValue],
    *,
    expected_name: str,
    expected_claim_name: str,
    expected_condition: str = "${{ github.event_name == 'workflow_dispatch' }}",
) -> None:
    named = _named_steps(job)
    claim = named["Upload redacted workflow receipt"]
    assert claim["if"] == expected_condition
    claim_options = _mapping(claim["with"])
    assert claim_options["name"] == expected_claim_name
    assert claim_options["retention-days"] == 1
    assert claim_options["if-no-files-found"] == "error"
    terminal = next(
        step for name, step in named.items() if name.startswith("Upload terminal ")
    )
    assert terminal["if"] == expected_condition
    terminal_options = _mapping(terminal["with"])
    assert terminal_options["name"] == expected_name
    assert terminal_options["retention-days"] == 1
    assert terminal_options["if-no-files-found"] == "error"
    assert "operation.json" in str(terminal_options["path"])


def _assert_no_production_database_credential(
    workflow: dict[str, JsonValue],
) -> None:
    rendered = yaml.safe_dump(workflow).upper()
    for forbidden in (
        "MIGRATION_DATABASE_URL",
        "DATABASE_URL: ${{ SECRETS.",
        "PG_DUMP_DATABASE_URL",
        "PG_RESTORE_DATABASE_URL",
        "SUPABASE_SERVICE",
        "SERVICE_ROLE",
    ):
        assert forbidden not in rendered


def _assert_no_database_credential(workflow: dict[str, JsonValue]) -> None:
    rendered = yaml.safe_dump(workflow).upper()
    for forbidden in (
        "DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "PG_DUMP_DATABASE_URL",
        "SUPABASE_SERVICE",
        "SERVICE_ROLE",
    ):
        assert forbidden not in rendered


def _assert_cadence_recording(
    job: dict[str, JsonValue], *, kind: str, operation_name: str
) -> None:
    steps = _steps(job)
    named = _named_steps(job)
    resolve = named["Resolve exact cadence branch"]
    operation = named[operation_name]
    record = named["Record durable cadence attempt"]
    upload = named["Upload public cadence receipt"]
    assert steps.index(resolve) < steps.index(operation) < steps.index(record)
    assert steps.index(record) < steps.index(upload)
    assert record["id"] == "cadence-record"
    assert record["if"] == (
        "${{ always() && (github.event_name == 'schedule' "
        "|| inputs.mode == 'cadence-retry') }}"
    )
    assert upload["if"] == (
        "${{ always() && steps.cadence-record.outcome == 'success' }}"
    )

    resolve_command = str(resolve["run"])
    assert f"slot --kind {kind}" in resolve_command
    assert 'mode="schedule"' in resolve_command
    assert 'mode="retry"' in resolve_command
    assert 'mode="manual"' in resolve_command
    assert 'test "$INPUT_ATTEMPT" = "2"' in resolve_command
    assert "CADENCE_FAILED_ATTEMPT" in resolve_command

    operation_environment = _mapping(operation["env"])
    assert operation_environment["MONITOR_CADENCE_RESULT_PATH"] == (
        "${{ runner.temp }}/cadence-operation-result.json"
    )
    assert operation_environment["MONITOR_CADENCE_SLOT_KEY"] == (
        "${{ env.CADENCE_SLOT_KEY }}"
    )

    record_command = str(record["run"])
    assert "release_cadence_workflow_client.py record" in record_command
    assert '--mode "$CADENCE_MODE"' in record_command
    assert '--cadence-attempt "$CADENCE_ATTEMPT"' in record_command
    assert '--failed-predecessor-attempt-id "$CADENCE_FAILED_ATTEMPT"' in (
        record_command
    )
    assert "$RUNNER_TEMP/cadence-operation-result.json" in record_command
    assert "$RUNNER_TEMP/cadence-attempt-receipt.json" in record_command
    assert "DATABASE_URL" not in record_command

    options = _mapping(upload["with"])
    assert options["retention-days"] == 1
    assert options["if-no-files-found"] == "error"
    assert options["path"] == "${{ runner.temp }}/cadence-attempt-receipt.json"
    assert "cadence-operation-result" not in str(options)


def test_ci_manual_dispatch_is_attempt_indexed_and_production_credential_free() -> None:
    workflow = _workflow("ci.yml")
    job = _job(workflow, "ci")

    assert workflow["run-name"] == (
        "ci-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}"
    )
    _assert_common_inputs(workflow)
    _assert_immutable_checkout(job)
    _assert_manual_validation(job)
    _assert_claim_contract(
        job, expected_environment="null", expected_workflow="ci.yml"
    )
    _assert_attempt_artifact(
        job,
        expected_name="ci-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}",
        expected_claim_name=(
            "ci-claim-${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}"
        ),
    )
    _assert_no_production_database_credential(workflow)

    concurrency = _mapping(workflow["concurrency"])
    assert concurrency["group"] == (
        "ci-${{ github.event_name == 'workflow_dispatch' "
        "&& inputs.dispatch_nonce || github.ref }}"
    )
    assert concurrency["cancel-in-progress"] == (
        "${{ github.event_name != 'workflow_dispatch' }}"
    )
    production = _job(workflow, "deploy-production")
    assert production["if"] == (
        "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' "
        "&& vars.PRODUCTION_AUTODEPLOY == 'enabled' }}"
    )


def test_ci_executes_shared_guarded_twenty_command_manifest() -> None:
    workflow = _workflow("ci.yml")
    job = _job(workflow, "ci")
    named = _named_steps(job)

    services = _mapping(job["services"])
    postgres = _mapping(services["postgres"])
    assert postgres["image"] == "postgres:17-alpine"
    assert postgres["ports"] == ["5432:5432"]
    service_env = _mapping(postgres["env"])
    assert service_env == {
        "POSTGRES_DB": "postgres",
        "POSTGRES_PASSWORD": "postgres",
        "POSTGRES_USER": "postgres",
    }
    assert "pg_isready -U postgres -d postgres" in str(postgres["options"])

    environment = _mapping(job["env"])
    assert environment == {
        "MIGRATION_QA_ADMIN_DATABASE_URL": (
            "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/postgres"
        ),
        "MIGRATION_QA_DATABASE_URL": (
            "postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/"
            "monitor_migration_qa"
        ),
    }
    rendered_environment = yaml.safe_dump(environment).lower()
    assert "supabase" not in rendered_environment
    assert "production" not in rendered_environment

    checkout = next(
        step
        for step in _steps(job)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert _mapping(checkout["with"])["fetch-depth"] == 0

    bindings = named["Bind immutable local-QA inputs"]
    binding_command = str(bindings["run"])
    assert 'reviewed_sha="$(git rev-parse HEAD)"' in binding_command
    assert 'test "$reviewed_sha" = "$EXPECTED_REVIEWED_SHA"' in binding_command
    assert "github.event.pull_request.base.sha" in str(bindings["env"])
    assert "github.event.before" in str(bindings["env"])
    assert "base_sha=$base_sha" in binding_command
    assert "reviewed_sha=$reviewed_sha" in binding_command
    assert "attempt_dir=$attempt_dir" in binding_command

    gate = named["Run guarded Todo 11 twenty-command manifest"]
    gate_command = str(gate["run"])
    assert gate_command.splitlines() == [
        "uv run --frozen --package monitor-api python \\",
        "  apps/api/scripts/local_qa_orchestrator.py \\",
        '  --attempt-dir "$ATTEMPT_DIR" \\',
        "  --database-admin-url-env MIGRATION_QA_ADMIN_DATABASE_URL \\",
        "  --database-url-env MIGRATION_QA_DATABASE_URL \\",
        '  --base-sha "$BASE_SHA" \\',
        '  --reviewed-sha "$REVIEWED_SHA" \\',
        "  --wrapper ci",
    ]
    gate_env = _mapping(gate["env"])
    assert gate_env == {
        "ATTEMPT_DIR": "${{ steps.local-qa-bindings.outputs.attempt_dir }}",
        "BASE_SHA": "${{ steps.local-qa-bindings.outputs.base_sha }}",
        "REVIEWED_SHA": "${{ steps.local-qa-bindings.outputs.reviewed_sha }}",
    }

    upload = named["Upload redacted local-QA receipts"]
    assert upload["if"] == "${{ always() }}"
    upload_options = _mapping(upload["with"])
    assert upload_options["if-no-files-found"] == "error"
    assert upload_options["retention-days"] == 1
    assert upload_options["path"] == (
        "${{ steps.local-qa-bindings.outputs.attempt_dir }}"
    )
    artifact_prefix = "-attempt-${{ github.event_name == 'workflow_dispatch' "
    artifact_suffix = f"{artifact_prefix}&& inputs.attempt || github.run_attempt }}}}"
    assert str(upload_options["name"]).endswith(artifact_suffix)

    workflow_text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert workflow_text.count("local_qa_orchestrator.py") == 1
    assert "Run API tests" not in named
    assert "Run web tests" not in named


def test_collect_preserves_schedule_and_protects_main_for_every_mode() -> None:
    workflow = _workflow("collect.yml")
    triggers = _mapping(workflow["on"])
    job = _job(workflow, "collect")

    assert triggers["schedule"] == [{"cron": "17 */3 * * *"}]
    assert workflow["run-name"] == (
        "collect-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}"
        "-attempt-${{ inputs.attempt }}"
    )
    assert job["if"] == "${{ github.ref == 'refs/heads/main' }}"
    _assert_common_inputs(workflow)
    mode = _mapping(_dispatch_inputs(workflow)["mode"])
    assert mode["required"] is True
    assert mode["type"] == "string"
    smoke = _mapping(_dispatch_inputs(workflow)["smoke_activation_at"])
    assert smoke["required"] is False
    assert smoke["type"] == "string"
    assert _mapping(job["env"])["PYTHONPATH"] == "apps/api"
    collection_environment = _mapping(
        _named_steps(job)["Collect through the scoped API"]["env"]
    )
    assert collection_environment["MONITOR_DEPLOYMENT_ACTIVATION_AT"] == (
        "${{ inputs.mode == 'direct-smoke' && inputs.smoke_activation_at "
        "|| vars.MONITOR_DEPLOYMENT_ACTIVATION_AT }}"
    )
    _assert_immutable_checkout(job)
    _assert_manual_validation(job)
    _assert_claim_contract(
        job,
        expected_environment='"production-collector"',
        expected_workflow="collect.yml",
        expected_condition=(
            "${{ github.event_name == 'workflow_dispatch' "
            "&& inputs.mode != 'direct-smoke' }}"
        ),
    )
    _assert_attempt_artifact(
        job,
        expected_name=(
            "collect-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}"
            "-attempt-${{ inputs.attempt }}"
        ),
        expected_claim_name=(
            "collect-claim-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}"
            "-attempt-${{ inputs.attempt }}"
        ),
        expected_condition=(
            "${{ github.event_name == 'workflow_dispatch' "
            "&& inputs.mode != 'direct-smoke' }}"
        ),
    )
    _assert_cadence_recording(
        job,
        kind="collection",
        operation_name="Collect through the scoped API",
    )


def test_verify_preserves_schedule_public_gate_and_read_only_claim() -> None:
    workflow = _workflow("verify.yml")
    triggers = _mapping(workflow["on"])
    job = _job(workflow, "verify")

    assert triggers["schedule"] == [{"cron": "*/15 * * * *"}]
    assert workflow["run-name"] == (
        "verify-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}"
        "-attempt-${{ inputs.attempt }}"
    )
    assert job["if"] == (
        "${{ github.event.repository.private == false || "
        "(github.event_name == 'workflow_dispatch' && "
        "inputs.authorize_private_minutes == true) }}"
    )
    _assert_common_inputs(workflow)
    mode = _mapping(_dispatch_inputs(workflow)["mode"])
    assert mode["required"] is True
    assert mode["type"] == "string"
    _assert_immutable_checkout(job)
    _assert_manual_validation(job)
    _assert_claim_contract(
        job,
        expected_environment='"production-verifier"',
        expected_workflow="verify.yml",
    )
    _assert_attempt_artifact(
        job,
        expected_name=(
            "verify-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}"
            "-attempt-${{ inputs.attempt }}"
        ),
        expected_claim_name=(
            "verify-claim-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}"
            "-attempt-${{ inputs.attempt }}"
        ),
    )
    _assert_cadence_recording(
        job,
        kind="verifier",
        operation_name="Verify freshness through the scoped API",
    )
    _assert_no_database_credential(workflow)


def test_todo11_workflows_have_no_hidden_retry_policy() -> None:
    rendered = "\n".join(
        yaml.safe_dump(_workflow(name))
        for name in ("ci.yml", "collect.yml", "verify.yml")
    )
    assert "continue-on-error: true" not in rendered
    assert "max-attempts" not in rendered
    assert "retry-action" not in rendered
    assert "--retry" not in rendered
    assert "sleep " not in rendered
    validation_commands = tuple(
        str(
            _named_steps(_job(_workflow(workflow_name), job_name))[
                "Validate immutable workflow identity"
            ]["run"]
        )
        for workflow_name, job_name in (
            ("ci.yml", "ci"),
            ("collect.yml", "collect"),
            ("verify.yml", "verify"),
        )
    )
    assert all(
        '[ "$ATTEMPT" = "1" ] || [ "$ATTEMPT" = "2" ]' in command
        for command in validation_commands
    )
