"""Adapters for immutable GitHub workflow dispatch and selection."""

# pyright: reportAny=false, reportArgumentType=false
# ruff: noqa: TC003

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.release_dispatch_bootstrap import bootstrap_dispatch
from scripts.release_dispatch_bootstrap_result import (
    bootstrap_select,
    bootstrap_verify,
)
from scripts.release_dispatch_receipts import verify_receipt
from scripts.release_dispatch_selector import RunIdentity, select_run
from scripts.release_dispatch_workflow import dispatch_workflow
from scripts.release_gate_cli_io import (
    read_bytes,
    read_document,
    write_document,
)
from scripts.release_gate_cli_subprocess import DispatchSubprocessRunner


def run_bootstrap_dispatch(args: argparse.Namespace) -> int:
    failed = (
        None
        if args.failed_attempt_receipt in {None, "none"}
        else read_bytes(args.failed_attempt_receipt)
    )
    receipt = bootstrap_dispatch(
        DispatchSubprocessRunner(),
        repository=args.repository,
        workflow=args.workflow,
        display_title=args.display_title,
        deployment_prestate=read_bytes(args.deployment_prestate),
        no_spend_receipt=read_bytes(args.no_spend_receipt),
        failed_attempt_receipt=failed,
        attempt=args.attempt,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=args.activation_nonce,
        dispatch_nonce=args.dispatch_nonce,
    )
    write_document(args.json_out, receipt)
    return 0


def run_bootstrap_select(args: argparse.Namespace) -> int:
    selection, operation = bootstrap_select(
        DispatchSubprocessRunner(),
        dispatch=read_document(args.dispatch),
        repository=args.repository,
        workflow=args.workflow,
        display_title=args.display_title,
        attempt=args.attempt,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=args.activation_nonce,
        dispatch_nonce=args.dispatch_nonce,
        sleep=time.sleep,
    )
    output = Path(args.json_out)
    write_document(str(output), selection)
    operation_path = output.with_name("operation.json")
    operation_path.parent.mkdir(parents=True, exist_ok=True)
    _ = operation_path.write_bytes(operation)
    return 0


def run_bootstrap_verify(args: argparse.Namespace) -> int:
    async def snapshot() -> dict[str, object]:
        url = os.environ.get(args.database_url_env)
        if not url:
            message = "database_url_environment_empty"
            raise ValueError(message)
        engine = create_async_engine(url)
        try:
            async with (
                engine.connect() as connection,
                connection.begin(),
            ):
                    _ = await connection.execute(
                        text(
                            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                        )
                    )
                    row = (
                        await connection.execute(
                            text(
                                """
                                SELECT
                                  (SELECT version_num FROM alembic_version)
                                    AS revision,
                                  to_regclass('public.release_roots') IS NOT NULL
                                    AS ledger_exists,
                                  EXISTS (
                                    SELECT 1 FROM community_sources
                                    WHERE platform::text = 'manifold'
                                  ) AS manifold_data_exists,
                                  EXISTS (
                                    SELECT 1 FROM pg_enum e
                                    JOIN pg_type t ON t.oid = e.enumtypid
                                    WHERE t.typname = 'source_platform'
                                      AND e.enumlabel = 'manifold'
                                  ) AS enum_residue
                                """
                            )
                        )
                    ).mappings().one()
                    return dict(row)
        finally:
            await engine.dispose()

    receipt = bootstrap_verify(
        read_bytes(args.operation),
        dispatch=read_document(args.dispatch),
        selection=read_document(args.selection),
        database_snapshot=anyio.run(snapshot),
        attempt=args.attempt,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=args.activation_nonce,
        dispatch_nonce=args.dispatch_nonce,
    )
    write_document(args.json_out, receipt)
    return 0


def run_dispatch_workflow(args: argparse.Namespace) -> int:
    receipt = dispatch_workflow(
        DispatchSubprocessRunner(),
        repository=args.repository,
        workflow_spec=read_bytes(args.workflow_spec),
        base=args.base,
        reservation=read_document(args.reservation),
        attempt=args.attempt,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=args.activation_nonce,
        dispatch_nonce=args.dispatch_nonce,
    )
    write_document(args.json_out, receipt)
    return 0


def run_select(args: argparse.Namespace) -> int:
    reservation = read_document(args.reservation)
    claimed = reservation.get("claimed_run_id")
    identity = RunIdentity(
        repository=args.repository,
        workflow=args.workflow,
        display_title=args.display_title,
        head_sha=args.expected_sha,
        activation_nonce=args.activation_nonce,
        dispatch_nonce=args.dispatch_nonce,
        attempt=args.attempt,
        selection_floor_at=str(reservation.get("selection_floor_at", "")),
        claimed_run_id=claimed if isinstance(claimed, int) else None,
    )
    receipt = select_run(
        DispatchSubprocessRunner(args.github_token_env),
        identity=identity,
        sleep=time.sleep,
    )
    write_document(args.json_out, receipt)
    return 0


def run_verify(args: argparse.Namespace) -> int:
    receipt = verify_receipt(
        read_bytes(args.receipt),
        selection=read_document(args.selection),
        reservation=read_document(args.reservation),
        expected_command=args.expected_command,
        attempt=args.attempt,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=args.activation_nonce,
        dispatch_nonce=args.dispatch_nonce,
    )
    write_document(args.json_out, receipt)
    return 0


HANDLERS = {
    "bootstrap-dispatch": run_bootstrap_dispatch,
    "bootstrap-select": run_bootstrap_select,
    "bootstrap-verify": run_bootstrap_verify,
    "dispatch-workflow": run_dispatch_workflow,
    "select-run": run_select,
    "verify-receipt": run_verify,
}

__all__ = ("HANDLERS",)
