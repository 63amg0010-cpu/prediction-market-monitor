"""Schema-owned post-ledger workflow dispatch."""

from __future__ import annotations

from typing import cast

from scripts.release_dispatch_contracts import (
    ChildRunner,
    HoldError,
    JsonObject,
    argv_sha256,
    canonical_bytes,
    copied_chain_fields,
    hold,
    load_canonical,
    run_once,
    sha256_hex,
    validate_common,
)

MAX_ATTEMPTS = 2


def _load_workflow(spec_raw: bytes, base: str) -> JsonObject:
    fixture = spec_raw[:-1] if spec_raw.endswith(b"\n") else spec_raw
    spec = load_canonical(fixture, max_bytes=32768)
    if set(spec) != {"schema_version", "workflows"} or spec["schema_version"] != 1:
        hold("workflow_spec_schema_invalid")
    workflows = spec.get("workflows")
    if not isinstance(workflows, dict) or base not in workflows:
        if base == "migrate-0010-bootstrap":
            hold("bootstrap_target_forbidden")
        hold("workflow_base_unknown")
    workflow = workflows[base]
    if not isinstance(workflow, dict):
        hold("workflow_spec_schema_invalid")
    required = {
        "artifact_template",
        "display_title_template",
        "max_attempts",
        "operation_inputs",
        "ref",
        "retry_contract",
        "workflow",
    }
    if set(workflow) != required or workflow["max_attempts"] != MAX_ATTEMPTS:
        hold("workflow_spec_schema_invalid")
    return cast("JsonObject", workflow)


def _render(template: object, values: JsonObject) -> str:
    if not isinstance(template, str):
        hold("workflow_template_invalid")
    try:
        return template.format_map(values)
    except KeyError as error:
        error_code = "workflow_template_binding_missing"
        raise HoldError(error_code) from error


def _field_argv(inputs: list[tuple[str, object]]) -> tuple[str, ...]:
    result: list[str] = []
    for key, value in inputs:
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        result.extend(("-f", f"{key}={rendered}"))
    return tuple(result)


def dispatch_workflow(  # noqa: PLR0913
    runner: ChildRunner,
    *,
    repository: str,
    workflow_spec: bytes,
    base: str,
    reservation: JsonObject,
    attempt: int,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
    dispatch_nonce: str,
) -> JsonObject:
    """Dispatch one schema-declared workflow once, with exact inputs."""
    workflow = _load_workflow(workflow_spec, base)
    validate_common(
        reservation,
        expected_sha=expected_sha,
        expected_plan_sha256=expected_plan_sha256,
        activation_nonce=activation_nonce,
    )
    values = {**reservation, "attempt": attempt, "dispatch_nonce": dispatch_nonce}
    title = _render(workflow["display_title_template"], values)
    if (
        attempt not in {1, 2}
        or reservation.get("attempt") != attempt
        or reservation.get("dispatch_nonce") != dispatch_nonce
        or reservation.get("display_title") != title
        or reservation.get("workflow_file") != workflow["workflow"]
    ):
        hold("reservation_dispatch_binding_mismatch")
    operation_inputs = workflow["operation_inputs"]
    if not isinstance(operation_inputs, dict):
        hold("workflow_spec_schema_invalid")
    rendered = [
        (str(key), _render(value, values))
        for key, value in operation_inputs.items()
    ]
    reservation_sha = sha256_hex(canonical_bytes(reservation))
    inputs: list[tuple[str, object]] = [
        *rendered,
        ("attempt", attempt),
        ("expected_commit_sha", expected_sha),
        ("expected_plan_sha256", expected_plan_sha256),
        ("activation_nonce", activation_nonce),
        ("dispatch_nonce", dispatch_nonce),
        ("reservation_sha256", reservation_sha),
    ]
    argv = (
        "gh",
        "workflow",
        "run",
        str(workflow["workflow"]),
        "--repo",
        repository,
        "--ref",
        str(workflow["ref"]),
        *_field_argv(inputs),
    )
    _ = run_once(runner, argv)
    return {
        "schema_version": 1,
        "command": "dispatch-workflow",
        "base": base,
        "attempt": attempt,
        **copied_chain_fields(reservation),
        "dispatch_nonce": dispatch_nonce,
        "reservation_receipt_sha256": reservation_sha,
        "workflow_file": workflow["workflow"],
        "display_title": title,
        "artifact_name": _render(workflow["artifact_template"], values),
        "argv_sha256": argv_sha256(argv),
        "redacted_argv": ["gh", "workflow", "run", workflow["workflow"]],
        "accepted": True,
        "terminal_for_attempt": False,
        "retry_permitted": False,
        "predecessor_receipt_sha256": reservation_sha,
    }


__all__ = ("dispatch_workflow",)
