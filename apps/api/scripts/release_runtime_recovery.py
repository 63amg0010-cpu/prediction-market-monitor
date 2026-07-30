"""Ledger-first recovery for an already completed release operation."""

# ruff: noqa: PLR0913
# pyright: reportAny=false, reportArgumentType=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import anyio
from sqlalchemy import text

from scripts.release_dispatch_contracts import (
    HoldError,
    canonical_bytes,
    load_canonical,
    sha256_hex,
)
from scripts.release_runtime_database import (
    engine_from_named_env,
    read_only_repeatable_read,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from scripts.release_runtime_subprocess import DispatchRuntimeRunner

LEDGER_RECEIPT = text(
    """
    SELECT canonical_receipt
    FROM release_operation_receipts
    WHERE run_id = :run_id
      AND activation_nonce = :activation_nonce
      AND dispatch_nonce = :dispatch_nonce
      AND attempt = :attempt
      AND head_sha = :head_sha
      AND receipt_sha256 = :receipt_sha256
    """
)


def recover_ledger_receipt(
    *,
    database_url_env: str,
    runner: DispatchRuntimeRunner,
    repository: str,
    workflow: str,
    original_run_id: int,
    operation: str,
    revision: str,
    attempt: int,
    dispatch_nonce: str,
    activation_nonce: str,
    expected_head_sha: str,
    expected_plan_sha256: str,
    expected_ledger_receipt_sha256: str,
) -> bytes:
    """Verify GitHub identity, then return only canonical bytes already in DB."""
    engine = engine_from_named_env(database_url_env)

    async def load() -> bytes:
        async def reader(connection: object, _observed_at: object) -> bytes:
            result = await connection.execute(  # type: ignore[attr-defined]
                LEDGER_RECEIPT,
                {
                    "activation_nonce": activation_nonce,
                    "attempt": attempt,
                    "dispatch_nonce": dispatch_nonce,
                    "head_sha": expected_head_sha,
                    "receipt_sha256": expected_ledger_receipt_sha256,
                    "run_id": original_run_id,
                },
            )
            raw = result.scalar_one_or_none()
            if not isinstance(raw, bytes):
                msg = "ledger_receipt_not_found"
                raise HoldError(msg)
            return raw

        try:
            return await read_only_repeatable_read(engine, reader)  # type: ignore[arg-type]
        finally:
            await engine.dispose()

    raw = anyio.run(load)
    if sha256_hex(raw) != expected_ledger_receipt_sha256:
        msg = "ledger_receipt_hash_mismatch"
        raise HoldError(msg)
    receipt = load_canonical(raw, max_bytes=32768)
    _verify_receipt_fields(
        receipt,
        run_id=original_run_id,
        operation=operation,
        revision=revision,
        attempt=attempt,
        dispatch_nonce=dispatch_nonce,
        activation_nonce=activation_nonce,
        expected_head_sha=expected_head_sha,
        expected_plan_sha256=expected_plan_sha256,
    )
    result = runner.run(
        (
            "gh",
            "api",
            f"/repos/{repository}/actions/runs/{original_run_id}",
        )
    )
    if result.returncode:
        msg = "recovery_run_verification_failed"
        raise HoldError(msg)
    try:
        run = cast("object", json.loads(result.stdout))
    except json.JSONDecodeError as error:
        msg = "recovery_run_verification_invalid"
        raise HoldError(msg) from error
    if not isinstance(run, dict):
        msg = "recovery_run_verification_invalid"
        raise HoldError(msg)
    if (
        run.get("id") != original_run_id
        or run.get("head_sha") != expected_head_sha
        or run.get("path") not in {workflow, f".github/workflows/{workflow}"}
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "completed"
    ):
        msg = "recovery_run_binding_mismatch"
        raise HoldError(msg)
    return canonical_bytes(receipt)


def _verify_receipt_fields(
    receipt: Mapping[str, object],
    *,
    run_id: int,
    operation: str,
    revision: str,
    attempt: int,
    dispatch_nonce: str,
    activation_nonce: str,
    expected_head_sha: str,
    expected_plan_sha256: str,
) -> None:
    expected = {
        "run_id": run_id,
        "operation": operation,
        "revision": revision,
        "attempt": attempt,
        "dispatch_nonce": dispatch_nonce,
        "activation_nonce": activation_nonce,
        "head_sha": expected_head_sha,
        "approved_plan_sha256": expected_plan_sha256,
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        msg = "ledger_receipt_binding_mismatch"
        raise HoldError(msg)


__all__ = ("recover_ledger_receipt",)
