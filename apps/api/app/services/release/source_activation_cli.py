"""Executable boundary for transactional source activation phases."""

# pyright: reportUnnecessaryComparison=false
# ruff: noqa: D101, D107

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, assert_never, final

import anyio
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from . import source_activation as activation_db
from .dispatch_reservation_cli import (
    add_dispatch_reserve_parser,
    execute_dispatch_reserve,
)
from .source_activation_commands import PhaseContext, commit, reserve
from .source_activation_domain import ActivationHoldError
from .source_activation_receipts import (
    ActivateRequest,
    ActivationOutput,
    ChainReceipt,
    write_output,
)
from .source_activation_recovery import reprepare, restore
from .source_activation_state import load_current_state

if TYPE_CHECKING:
    from collections.abc import Sequence


@final
class ActivateCliArgs(argparse.Namespace):
    def __init__(self) -> None:
        super().__init__()
        self.command: str = ""
        self.phase: str = ""
        self.database_url_env: str = ""
        self.activation_nonce: str = ""
        self.expected_sha: str = ""
        self.json_out: str = ""
        self.attestation: str | None = None
        self.free_tier_result: str | None = None
        self.binding_handshake_receipt: str | None = None
        self.activation_reserve_receipt: str | None = None
        self.binding_finalize_receipt: str | None = None
        self.attestation_generation: int | None = None
        self.failed_reservation_receipt: str | None = None
        self.previous_attestation_receipt: str | None = None
        self.activation_evidence_receipt: str | None = None
        self.binding_restore_receipt: str | None = None
        self.restore_verification_receipt: str | None = None
        self.repository: str = ""
        self.workflow: str = ""
        self.display_title: str = ""
        self.head_sha: str = ""
        self.expected_plan_sha256: str = ""
        self.dispatch_nonce: str = ""
        self.predecessor_receipt: str = ""
        self.attempt: int = 0
        self.git_ref: str = "refs/heads/main"


def parser() -> argparse.ArgumentParser:
    """Build the committed activate command parser."""
    root = argparse.ArgumentParser()
    subcommands = root.add_subparsers(dest="command", required=True)
    add_dispatch_reserve_parser(subcommands)
    activate = subcommands.add_parser("activate")
    _ = activate.add_argument(
        "--phase",
        required=True,
        choices=("reserve", "commit", "reprepare", "restore"),
    )
    for name in (
        "database-url-env",
        "activation-nonce",
        "expected-sha",
        "json-out",
    ):
        _ = activate.add_argument(f"--{name}", required=True)
    for name in (
        "attestation",
        "free-tier-result",
        "binding-handshake-receipt",
        "activation-reserve-receipt",
        "binding-finalize-receipt",
        "failed-reservation-receipt",
        "previous-attestation-receipt",
        "activation-evidence-receipt",
        "binding-restore-receipt",
        "restore-verification-receipt",
    ):
        _ = activate.add_argument(f"--{name}")
    _ = activate.add_argument("--attestation-generation", type=int)
    return root


def parse_args(argv: Sequence[str] | None = None) -> ActivateCliArgs:
    """Parse operator argv into a fully declared namespace."""
    return parser().parse_args(argv, namespace=ActivateCliArgs())


def _request(args: ActivateCliArgs) -> ActivateRequest:
    return ActivateRequest.model_validate(
        {
            "phase": args.phase,
            "database_url_env": args.database_url_env,
            "activation_nonce": args.activation_nonce,
            "expected_sha": args.expected_sha,
            "json_out": Path(args.json_out),
            "attestation": args.attestation,
            "free_tier_result": args.free_tier_result,
            "binding_handshake_receipt": args.binding_handshake_receipt,
            "activation_reserve_receipt": args.activation_reserve_receipt,
            "binding_finalize_receipt": args.binding_finalize_receipt,
            "attestation_generation": args.attestation_generation,
            "failed_reservation_receipt": args.failed_reservation_receipt,
            "previous_attestation_receipt": args.previous_attestation_receipt,
            "activation_evidence_receipt": args.activation_evidence_receipt,
            "binding_restore_receipt": args.binding_restore_receipt,
            "restore_verification_receipt": args.restore_verification_receipt,
        }
    )


async def execute(args: ActivateCliArgs) -> int:
    """Run one phase in a caller-owned database transaction."""
    if args.command == "dispatch-reserve":
        return await execute_dispatch_reserve(args)
    request = _request(args)
    database_url = os.environ.get(request.database_url_env)
    if not database_url:
        error_code = "database_url_environment_empty"
        raise ActivationHoldError(error_code)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            db_now = await activation_db.database_now_locked(connection)
            locked = await load_current_state(connection)
            if locked.state.activation_nonce != request.activation_nonce:
                error_code = "activation_nonce_mismatch"
                raise ActivationHoldError(error_code)
            context = PhaseContext(connection, request, locked, db_now)
            match request.phase:
                case "reserve":
                    output: ActivationOutput = await reserve(context)
                case "commit":
                    output = await commit(context)
                case "reprepare":
                    output = await reprepare(context)
                case "restore":
                    output = await restore(context)
                case unreachable:
                    assert_never(unreachable)
    finally:
        await engine.dispose()
    write_output(request.json_out, output)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the release gate and emit only bounded redacted HOLD codes."""
    try:
        return anyio.run(execute, parse_args(argv))
    except (
        ActivationHoldError,
        OSError,
        RuntimeError,
        SQLAlchemyError,
        TypeError,
        ValidationError,
        ValueError,
    ) as error:
        _ = sys.stderr.write(f"activation HOLD: {error}\n")
        return 2


__all__ = (
    "ActivateCliArgs",
    "ActivateRequest",
    "ChainReceipt",
    "execute",
    "main",
    "parse_args",
    "parser",
)
