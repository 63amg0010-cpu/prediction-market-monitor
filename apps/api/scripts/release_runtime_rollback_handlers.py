"""CLI handlers for compatibility and Matrix-B rollback observations."""

# ruff: noqa: PLR2004
# pyright: reportAny=false, reportArgumentType=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportGeneralTypeIssues=false, reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import anyio

from scripts.release_gate_cli_io import read_document, write_document
from scripts.release_rollback_models import (
    MatrixBHealthInput,
    RollbackMutationIntent,
)
from scripts.release_rollback_validation import validate_matrix_b_health
from scripts.release_runtime_compat_handler import compat_state
from scripts.release_runtime_database import engine_from_named_env
from scripts.release_runtime_http import ReadOnlyHttpProbe
from scripts.release_runtime_mutations import TransactionalRollbackAdapter
from scripts.release_runtime_rollback import (
    deployment_state,
    health_state,
    rollback_database_snapshot,
)
from scripts.release_vercel_models import seal_receipt, verify_receipt

if TYPE_CHECKING:
    import argparse


def _load_states(args: argparse.Namespace) -> tuple[object, object, object, object]:
    engine = engine_from_named_env(args.database_url_env)

    async def load() -> object:
        try:
            return await rollback_database_snapshot(
                engine, UUID(args.activation_nonce)
            )
        finally:
            await engine.dispose()

    snapshot = anyio.run(load)
    api_receipt = read_document(args.api_receipt)
    web_receipt = read_document(args.web_receipt)
    api = deployment_state(api_receipt, expected_kind="api")
    web = deployment_state(web_receipt, expected_kind="web")
    health, raw_health = health_state(
        args.api_url,
        args.web_url,
        probe=ReadOnlyHttpProbe(),
    )
    return snapshot, api, web, (health, raw_health)


def matrix_b_health(args: argparse.Namespace) -> int:
    """Capture read-only Matrix-B DB/deployment/health state."""
    if not args.read_only:
        msg = "matrix_b_read_only_required"
        raise ValueError(msg)
    snapshot, api, web, health_values = _load_states(args)
    database = snapshot.state
    health, _raw = health_values
    if database.revision != args.expected_current:
        msg = "matrix_b_expected_current_mismatch"
        raise ValueError(msg)
    request = MatrixBHealthInput(
        database=database,
        api=api,
        web=web,
        health=health,
        downgrade_receipt=read_document(args.downgrade_receipt),
        binding_restore_receipt=read_document(args.binding_restore_receipt),
        api_receipt=read_document(args.api_receipt),
        web_receipt=read_document(args.web_receipt),
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
        predecessor_receipt=read_document(args.predecessor_receipt),
    )
    write_document(args.json_out, validate_matrix_b_health(request))
    return 0


def rollback_finalize(args: argparse.Namespace) -> int:
    """CAS terminal restored only from a verified technical Matrix-B chain."""
    if args.incident_class != "technical":
        msg = "privacy_incident_requires_privacy_verify"
        raise ValueError(msg)
    chain = read_document(args.matrix_b_chain)
    predecessor = read_document(args.predecessor_receipt)
    chain_sha = verify_receipt(
        chain,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
    )
    predecessor_sha = verify_receipt(
        predecessor,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
    )
    details = chain.get("details")
    if (
        chain.get("command") != "materialize-chain"
        or predecessor_sha != chain_sha
        or not isinstance(details, dict)
        or details.get("terminal_command") != "matrix-b-health"
        or details.get("node_count") != 6
    ):
        msg = "invalid_matrix_b_chain"
        raise ValueError(msg)
    engine = engine_from_named_env(args.database_url_env)

    async def finalize() -> dict[str, object]:
        database = (
            await rollback_database_snapshot(
                engine, UUID(args.activation_nonce)
            )
        ).state
        if (
            database.revision != "20260727_0010"
            or database.latest_transition != "restore_writing"
        ):
            msg = "rollback_database_state_invalid"
            raise ValueError(msg)
        body = seal_receipt(
            {
                "schema_version": 1,
                "command": "rollback-finalize",
                "reviewed_sha": args.expected_sha,
                "approved_plan_sha256": args.expected_plan_sha256,
                "activation_nonce": args.activation_nonce,
                "predecessor_receipt_sha256": chain_sha,
                "matrix_b_chain_sha256": chain_sha,
                "incident_class": "technical",
                "expected_transition_id": database.latest_transition_id,
                "state_before": "restore_writing",
                "state_after": "restored",
                "accepted": True,
            }
        )
        intent = RollbackMutationIntent(
            "source-binding",
            UUID(args.activation_nonce),
            "restore_writing",
            database.latest_transition_id,
            "restored",
            "technical",
            chain_sha,
            chain_sha,
            body,
        )
        await TransactionalRollbackAdapter(engine).finalize(intent)
        return body

    try:
        receipt = anyio.run(finalize)
    finally:
        anyio.run(engine.dispose)
    write_document(args.json_out, receipt)
    return 0


HANDLERS = {
    "compat-state": compat_state,
    "matrix-b-health": matrix_b_health,
    "rollback-finalize": rollback_finalize,
}

__all__ = ("HANDLERS",)
