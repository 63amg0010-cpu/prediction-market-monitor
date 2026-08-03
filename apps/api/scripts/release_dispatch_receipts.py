"""Operation artifact recovery and immutable receipt verification."""

from __future__ import annotations

from scripts.release_dispatch_contracts import (
    ChildRunner,
    JsonObject,
    hold,
    load_canonical,
    run_once,
    sha256_hex,
    validate_common,
)


def verify_receipt(  # noqa: PLR0913
    raw: bytes,
    *,
    selection: JsonObject,
    reservation: JsonObject,
    expected_command: str,
    attempt: int,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
    dispatch_nonce: str,
) -> JsonObject:
    """Validate a canonical operation artifact against its complete subchain."""
    operation = load_canonical(raw, max_bytes=32768)
    validate_common(
        operation,
        expected_sha=expected_sha,
        expected_plan_sha256=expected_plan_sha256,
        activation_nonce=activation_nonce,
    )
    reservation_sha = reservation.get("receipt_sha256")
    if not isinstance(reservation_sha, str):
        hold("reservation_receipt_sha256_invalid")
    accepted = operation.get("accepted")
    terminal = operation.get("terminal_for_attempt")
    retry = operation.get("retry_permitted")
    if (
        operation.get("command") != expected_command
        or operation.get("attempt") != attempt
        or operation.get("dispatch_nonce") != dispatch_nonce
        or operation.get("run_id") != selection.get("databaseId")
        or operation.get("reservation_receipt_sha256") != reservation_sha
        or operation.get("predecessor_receipt_sha256") != reservation_sha
        or accepted not in {True, False}
        or terminal is not True
        or (accepted is True and retry is not False)
        or (accepted is False and retry is not True)
    ):
        hold("operation_receipt_binding_mismatch")
    return {
        **operation,
        "command": "verify-receipt",
        "verified_command": expected_command,
        "artifact_sha256": sha256_hex(raw),
        "selection_databaseId": selection["databaseId"],
    }


def recover_operation_receipt(
    runner: ChildRunner,
    *,
    repository: str,
    artifact_name: str,
    selection: JsonObject,
) -> bytes:
    """Recover one lost artifact without retrying or redispatching its workflow."""
    if (
        selection.get("status") != "completed"
        or selection.get("conclusion") not in {"success", "failure"}
        or not isinstance(selection.get("databaseId"), int)
        or "-attempt-" not in artifact_name
    ):
        hold("recovery_selection_invalid")
    argv = (
        "gh",
        "run",
        "download",
        str(selection["databaseId"]),
        "--repo",
        repository,
        "--name",
        artifact_name,
    )
    result = run_once(runner, argv)
    raw = result.stdout.encode()
    _ = load_canonical(raw, max_bytes=32768)
    return raw


__all__ = ("recover_operation_receipt", "verify_receipt")
