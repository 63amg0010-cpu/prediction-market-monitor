"""Fail-closed dispatch and redacted error boundary for the unified CLI."""

# pyright: reportAny=false, reportArgumentType=false
# ruff: noqa: E402, EM101, EM102, I001

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import anyio
from app.services.release.source_activation_cli import execute as execute_activation

from scripts.release_gate_cli_attestation import HANDLERS as ATTESTATION_HANDLERS
from scripts.release_gate_cli_cadence import HANDLERS as CADENCE_HANDLERS
from scripts.release_gate_cli_chain import HANDLERS as CHAIN_HANDLERS
from scripts.release_gate_cli_dispatch import HANDLERS as DISPATCH_HANDLERS
from scripts.release_gate_cli_evidence import HANDLERS as EVIDENCE_HANDLERS
from scripts.release_gate_cli_parser import parse_args
from scripts.release_gate_cli_preflight import HANDLERS as PREFLIGHT_HANDLERS
from scripts.release_gate_cli_static import HANDLERS as STATIC_HANDLERS
from scripts.release_gate_cli_vercel import HANDLERS as VERCEL_HANDLERS
from scripts.release_runtime_handlers import runtime_handlers
from scripts.release_runtime_rollback_handlers import (
    HANDLERS as ROLLBACK_RUNTIME_HANDLERS,
)
from scripts.runtime_production_adapter_cli import HANDLERS as PRODUCTION_HANDLERS
from scripts.runtime_privacy_adapter_cli import HANDLERS as PRIVACY_HANDLERS

Handler = Callable[[argparse.Namespace], int]
_SAFE_CODE = re.compile(r"^[a-zA-Z0-9_.:-]{1,120}$")


class ReleaseGateHoldError(RuntimeError):
    """The requested command has no safe concrete adapter."""


def _activation(args: argparse.Namespace) -> int:
    return anyio.run(execute_activation, args)


def default_handlers() -> dict[str, Handler]:
    """Return concrete adapters that are safe in the current process."""
    return {
        **STATIC_HANDLERS,
        **EVIDENCE_HANDLERS,
        **CHAIN_HANDLERS,
        **CADENCE_HANDLERS,
        **DISPATCH_HANDLERS,
        **ATTESTATION_HANDLERS,
        **VERCEL_HANDLERS,
        **PREFLIGHT_HANDLERS,
        **PRODUCTION_HANDLERS,
        **PRIVACY_HANDLERS,
        "activate": _activation,
        "dispatch-reserve": _activation,
        **runtime_handlers(),
        **ROLLBACK_RUNTIME_HANDLERS,
    }


def execute(
    args: argparse.Namespace,
    handlers: Mapping[str, Handler] | None = None,
) -> int:
    """Execute exactly one registered handler, allowing explicit test injection."""
    command = str(args.command)
    selected = handlers if handlers is not None else default_handlers()
    handler = selected.get(command)
    if handler is None:
        raise ReleaseGateHoldError(f"{command.replace('-', '_')}_adapter_missing")
    result = handler(args)
    if isinstance(result, bool):
        raise ReleaseGateHoldError("release_gate_handler_result_invalid")
    return result


def _redacted_code(error: Exception) -> str:
    value = str(error)
    if _SAFE_CODE.fullmatch(value):
        return value
    return f"{type(error).__name__.lower()}_redacted"


def main(
    argv: Sequence[str] | None = None,
    *,
    handlers: Mapping[str, Handler] | None = None,
) -> int:
    """Parse, dispatch, and reduce every exception to one bounded HOLD code."""
    command = ""
    try:
        args = parse_args(argv)
        command = str(args.command)
        return execute(args, handlers)
    except Exception as error:  # noqa: BLE001 - executable fail-closed boundary
        label = "activation" if command == "activate" else "release gate"
        _ = sys.stderr.write(f"{label} HOLD: {_redacted_code(error)}\n")
        return 2


__all__ = (
    "Handler",
    "ReleaseGateHoldError",
    "default_handlers",
    "execute",
    "main",
)
