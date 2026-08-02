"""Credential-free immutable migration-0010 bootstrap dispatch."""

from __future__ import annotations

import base64
from email.utils import parsedate_to_datetime

from scripts.release_dispatch_contracts import (
    ChildRunner,
    JsonObject,
    argv_sha256,
    copied_chain_fields,
    hold,
    load_canonical,
    run_once,
    sha256_hex,
    validate_common,
)

ATTEMPT_TWO = 2
NO_SPEND_FIELDS = frozenset(
    {
        "schema_version",
        "command",
        "reviewed_sha",
        "approved_plan_sha256",
        "activation_nonce",
        "predecessor_receipt_sha256",
        "billing_disabled",
        "projection_below_70_percent",
    }
)


def _field_argv(inputs: list[tuple[str, object]]) -> tuple[str, ...]:
    result: list[str] = []
    for key, value in inputs:
        result.extend(("-f", f"{key}={value}"))
    return tuple(result)


def _server_floor(headers: str) -> str:
    dates = [
        line.split(":", 1)[1].strip()
        for line in headers.splitlines()
        if line.lower().startswith("date:")
    ]
    if len(dates) != 1:
        hold("github_date_header_invalid")
    try:
        value = parsedate_to_datetime(dates[0])
    except (TypeError, ValueError) as error:
        error_code = "github_date_header_invalid"
        raise ValueError(error_code) from error
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _attempt_proof(attempt: int, failed: bytes | None) -> tuple[str, str]:
    if attempt == 1:
        if failed is not None:
            hold("attempt_one_failed_receipt_forbidden")
        return "", ""
    if attempt != ATTEMPT_TWO or failed is None:
        hold("attempt_two_failed_receipt_required")
    proof = load_canonical(failed)
    if (
        proof.get("attempt") != 1
        or proof.get("accepted") is not False
        or proof.get("terminal_for_attempt") is not True
        or proof.get("retry_permitted") is not True
        or proof.get("state_before") != "20260726_0009"
        or proof.get("state_after") != "20260726_0009"
        or proof.get("ledger_exists") is not False
        or proof.get("manifold_data_exists") is not False
    ):
        hold("attempt_two_proof_invalid")
    return sha256_hex(failed), base64.b64encode(failed).decode()


def _validate_no_spend(
    value: JsonObject,
    *,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
    root_sha256: str,
) -> None:
    """Validate the exact schema transported to the migration boundary."""
    if (
        frozenset(value) != NO_SPEND_FIELDS
        or value.get("schema_version") != 1
        or value.get("command") != "no-spend-preflight"
        or value.get("reviewed_sha") != expected_sha
        or value.get("approved_plan_sha256") != expected_plan_sha256
        or value.get("activation_nonce") != activation_nonce
        or value.get("predecessor_receipt_sha256") != root_sha256
        or value.get("billing_disabled") is not True
        or value.get("projection_below_70_percent") is not True
    ):
        hold("no_spend_receipt_invalid")


def bootstrap_dispatch(  # noqa: PLR0913
    runner: ChildRunner,
    *,
    repository: str,
    workflow: str,
    display_title: str,
    deployment_prestate: bytes,
    no_spend_receipt: bytes,
    failed_attempt_receipt: bytes | None,
    attempt: int,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
    dispatch_nonce: str,
) -> JsonObject:
    """Capture GitHub server time, then dispatch bootstrap exactly once."""
    root = load_canonical(deployment_prestate)
    no_spend = load_canonical(no_spend_receipt)
    validate_common(
        root,
        expected_sha=expected_sha,
        expected_plan_sha256=expected_plan_sha256,
        activation_nonce=activation_nonce,
    )
    root_sha = sha256_hex(deployment_prestate)
    no_spend_sha = sha256_hex(no_spend_receipt)
    _validate_no_spend(
        no_spend,
        expected_sha=expected_sha,
        expected_plan_sha256=expected_plan_sha256,
        activation_nonce=activation_nonce,
        root_sha256=root_sha,
    )
    failed_sha, failed_b64 = _attempt_proof(attempt, failed_attempt_receipt)
    expected_title = (
        f"migrate-upgrade-20260727_0010-{dispatch_nonce}-attempt-{attempt}"
    )
    if workflow != "migrate.yml" or display_title != expected_title:
        hold("bootstrap_identity_invalid")
    rate = run_once(runner, ("gh", "api", "-i", "/rate_limit"))
    floor = _server_floor(rate.stdout)
    inputs: list[tuple[str, object]] = [
        ("operation", "upgrade"),
        ("revision", "20260727_0010"),
        ("confirm", "migrate-production"),
        ("attempt", attempt),
        ("expected_commit_sha", expected_sha),
        ("expected_plan_sha256", expected_plan_sha256),
        ("activation_nonce", activation_nonce),
        ("dispatch_nonce", dispatch_nonce),
        ("review_root_sha256", root_sha),
        ("review_root_b64", base64.b64encode(deployment_prestate).decode()),
        ("no_spend_receipt_sha256", no_spend_sha),
        ("no_spend_receipt_b64", base64.b64encode(no_spend_receipt).decode()),
        ("attempt1_failed_receipt_sha256", failed_sha),
        ("attempt1_failed_receipt_b64", failed_b64),
    ]
    argv = (
        "gh",
        "workflow",
        "run",
        workflow,
        "--repo",
        repository,
        "--ref",
        "main",
        *_field_argv(inputs),
    )
    _ = run_once(runner, argv)
    return {
        "schema_version": 1,
        "command": "bootstrap-dispatch",
        "attempt": attempt,
        **copied_chain_fields(root),
        "dispatch_nonce": dispatch_nonce,
        "display_title": display_title,
        "workflow_file": workflow,
        "selection_floor_at": floor,
        "selection_floor_response_sha256": sha256_hex(rate.stdout.encode()),
        "review_root_sha256": root_sha,
        "no_spend_receipt_sha256": no_spend_sha,
        "attempt1_failed_receipt_sha256": failed_sha or None,
        "state_before": "20260726_0009",
        "state_after": "20260726_0009",
        "accepted": True,
        "terminal_for_attempt": False,
        "retry_permitted": False,
        "predecessor_receipt_sha256": no_spend_sha,
        "argv_sha256": argv_sha256(argv),
    }


__all__ = ("bootstrap_dispatch",)
