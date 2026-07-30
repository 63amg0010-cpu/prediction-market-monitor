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
    job: dict[str, JsonValue], *, expected_environment: str, expected_workflow: str
) -> None:
    step = _named_steps(job)["Claim durable workflow reservation"]
    assert step["if"] == "${{ github.event_name == 'workflow_dispatch' }}"
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
    job: dict[str, JsonValue], *, expected_name: str
) -> None:
    step = _named_steps(job)["Upload redacted workflow receipt"]
    assert step["if"] == "${{ github.event_name == 'workflow_dispatch' }}"
    options = _mapping(step["with"])
    assert options["name"] == expected_name
    assert options["retention-days"] == 1
    assert options["if-no-files-found"] == "error"


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


def test_ci_manual_dispatch_is_attempt_indexed_and_credential_free() -> None:
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
    )
    _assert_no_database_credential(workflow)

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
        "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}"
    )


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
    _assert_immutable_checkout(job)
    _assert_manual_validation(job)
    _assert_claim_contract(
        job,
        expected_environment='"production-collector"',
        expected_workflow="collect.yml",
    )
    _assert_attempt_artifact(
        job,
        expected_name=(
            "collect-${{ inputs.mode }}-${{ inputs.dispatch_nonce }}"
            "-attempt-${{ inputs.attempt }}"
        ),
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
