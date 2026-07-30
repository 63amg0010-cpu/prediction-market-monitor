"""Pre-ledger bootstrap selection and terminal verification."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scripts.release_dispatch_contracts import (
    ChildRunner,
    JsonObject,
    copied_chain_fields,
    hold,
    load_canonical,
    sha256_hex,
    validate_common,
)
from scripts.release_dispatch_receipts import recover_operation_receipt
from scripts.release_dispatch_selector import RunIdentity, select_run

if TYPE_CHECKING:
    from collections.abc import Callable


def bootstrap_select(  # noqa: PLR0913
    runner: ChildRunner,
    *,
    dispatch: JsonObject,
    repository: str,
    workflow: str,
    display_title: str,
    attempt: int,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
    dispatch_nonce: str,
    sleep: Callable[[float], None],
) -> tuple[JsonObject, bytes]:
    """Select, watch, and recover the unique pre-ledger operation artifact."""
    validate_common(
        dispatch,
        expected_sha=expected_sha,
        expected_plan_sha256=expected_plan_sha256,
        activation_nonce=activation_nonce,
    )
    expected_title = (
        f"migrate-upgrade-20260727_0010-{dispatch_nonce}-attempt-{attempt}"
    )
    if (
        workflow != "migrate.yml"
        or display_title != expected_title
        or dispatch.get("workflow_file") != workflow
        or dispatch.get("display_title") != display_title
        or dispatch.get("attempt") != attempt
        or dispatch.get("dispatch_nonce") != dispatch_nonce
    ):
        hold("bootstrap_selection_binding_mismatch")
    selection = select_run(
        runner,
        identity=RunIdentity(
            repository=repository,
            workflow=workflow,
            display_title=display_title,
            head_sha=expected_sha,
            activation_nonce=activation_nonce,
            dispatch_nonce=dispatch_nonce,
            attempt=attempt,
            selection_floor_at=str(dispatch["selection_floor_at"]),
            claimed_run_id=None,
        ),
        sleep=sleep,
    )
    operation = recover_operation_receipt(
        runner,
        repository=repository,
        artifact_name=f"migrate-0010-bootstrap-{dispatch_nonce}-attempt-{attempt}",
        selection=selection,
    )
    return selection, operation


def bootstrap_verify(  # noqa: PLR0913
    operation_raw: bytes,
    *,
    dispatch: JsonObject,
    selection: JsonObject,
    database_snapshot: JsonObject,
    attempt: int,
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
    dispatch_nonce: str,
) -> JsonObject:
    """Verify the special 0009/0010 bootstrap outcome without doing I/O."""
    operation = load_canonical(operation_raw, max_bytes=32768)
    for value in (dispatch, operation):
        validate_common(
            value,
            expected_sha=expected_sha,
            expected_plan_sha256=expected_plan_sha256,
            activation_nonce=activation_nonce,
        )
    accepted = operation.get("accepted")
    success = (
        accepted is True
        and database_snapshot.get("revision") == "20260727_0010"
        and database_snapshot.get("ledger_exists") is True
        and database_snapshot.get("manifold_data_exists") is False
    )
    failure = (
        accepted is False
        and database_snapshot.get("revision") == "20260726_0009"
        and database_snapshot.get("ledger_exists") is False
        and database_snapshot.get("manifold_data_exists") is False
        and operation.get("terminal_for_attempt") is True
        and operation.get("retry_permitted") is True
    )
    if (
        operation.get("attempt") != attempt
        or operation.get("dispatch_nonce") != dispatch_nonce
        or operation.get("run_id") != selection.get("databaseId")
        or operation.get("review_root_sha256") != dispatch.get("review_root_sha256")
        or operation.get("no_spend_receipt_sha256")
        != dispatch.get("no_spend_receipt_sha256")
        or not (success or failure)
    ):
        hold("bootstrap_outcome_invalid")
    return {
        "schema_version": 1,
        "command": "bootstrap-verify",
        "attempt": attempt,
        **copied_chain_fields(dispatch),
        "dispatch_nonce": dispatch_nonce,
        "run_id": selection["databaseId"],
        "artifact_sha256": sha256_hex(operation_raw),
        "review_root_sha256": dispatch["review_root_sha256"],
        "no_spend_receipt_sha256": dispatch["no_spend_receipt_sha256"],
        "backup_sha256": operation.get("backup_sha256"),
        "state_before": "20260726_0009",
        "state_after": database_snapshot["revision"],
        "ledger_exists": database_snapshot["ledger_exists"],
        "manifold_data_exists": database_snapshot["manifold_data_exists"],
        "enum_residue": database_snapshot.get("enum_residue", False),
        "accepted": accepted,
        "terminal_for_attempt": True,
        "retry_permitted": accepted is False,
        "predecessor_receipt_sha256": sha256_hex(operation_raw),
    }


__all__ = ("bootstrap_select", "bootstrap_verify")
