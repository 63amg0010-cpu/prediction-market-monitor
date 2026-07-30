"""Adapters for filesystem-backed release-chain handlers."""

# pyright: reportAny=false, reportArgumentType=false, reportCallIssue=false
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false
# ruff: noqa: PLR2004, TC001, TC003

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import cast

from scripts.release_chain import (
    AcceptanceInputManifestRequest,
    AcceptanceRefreshRequest,
    AggregateRequest,
    FinalFanInRequest,
    FinalLaneRequest,
    MaterializeChainRequest,
    NamedPath,
    handle_acceptance_input_manifest,
    handle_acceptance_refresh,
    handle_aggregate,
    handle_final_fan_in,
    handle_final_lane,
    handle_materialize_chain,
)
from scripts.release_chain_common import Clock
from scripts.release_chain_final import FinalLane
from scripts.release_gate_cli_database import with_database_clock
from scripts.release_gate_cli_io import csv, strings
from scripts.release_runtime_io import BoundedPathReceiptIO

_IO = BoundedPathReceiptIO()


def _run(
    args: argparse.Namespace,
    handler: Callable[[Clock], object],
) -> int:
    _ = with_database_clock(args.database_url_env, handler)
    return 0


def materialize(args: argparse.Namespace) -> int:
    request = MaterializeChainRequest(
        Path(args.manifest), Path(args.receipt_root), args.expected_terminal_command,
        args.expected_sha, args.expected_plan_sha256, args.activation_nonce,
        Path(args.predecessor_receipt), Path(args.json_out),
    )
    return _run(
        args, lambda clock: handle_materialize_chain(request, io=_IO, clock=clock)
    )


def final_lane(args: argparse.Namespace) -> int:
    request = FinalLaneRequest(
        lane=cast("FinalLane", args.lane),
        report=Path(args.report),
        production_result=Path(args.production_result),
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=args.activation_nonce,
        predecessor_receipt=Path(args.predecessor_receipt),
        json_out=Path(args.json_out),
        aux_report=None if args.aux_report is None else Path(args.aux_report),
        cadence=None if args.cadence is None else Path(args.cadence),
    )
    return _run(args, lambda clock: handle_final_lane(request, io=_IO, clock=clock))


def final_fan_in(args: argparse.Namespace) -> int:
    request = FinalFanInRequest(
        Path(args.parent), tuple(Path(v) for v in strings(args.branch)),
        csv(args.expected_branches), args.expected_sha,
        args.expected_plan_sha256, args.activation_nonce,
        Path(args.predecessor_receipt), Path(args.json_out),
    )
    return _run(
        args, lambda clock: handle_final_fan_in(request, io=_IO, clock=clock)
    )


def aggregate(args: argparse.Namespace) -> int:
    request = AggregateRequest(
        Path(args.fan_in), Path(args.f4), Path(args.cadence), args.expected_sha,
        args.expected_plan_sha256, args.activation_nonce,
        Path(args.predecessor_receipt), Path(args.json_out),
        None if args.acceptance_refresh is None else Path(args.acceptance_refresh),
    )
    return _run(args, lambda clock: handle_aggregate(request, io=_IO, clock=clock))


def _input_members(args: argparse.Namespace) -> tuple[NamedPath, ...]:
    providers = strings(args.provider_capture)
    if len(providers) != 4:
        message = "provider_capture_count_invalid"
        raise ValueError(message)
    return (
        NamedPath("manifold-evidence.json", Path(args.authorization_evidence)),
        NamedPath("production-free-tier.json", Path(args.provider_manifest)),
        NamedPath("free-tier-measurements.json", Path(args.local_measurements)),
        *(NamedPath(Path(value).name, Path(value)) for value in providers),
        NamedPath(
            "production-db-measurements.json", Path(args.production_measurements)
        ),
    )


def acceptance_input(args: argparse.Namespace) -> int:
    request = AcceptanceInputManifestRequest(
        _input_members(args), args.expected_sha, args.expected_plan_sha256,
        args.activation_nonce, Path(args.predecessor_receipt),
        Path(args.output_root), Path(args.json_out),
    )
    return _run(
        args,
        lambda clock: handle_acceptance_input_manifest(
            request, io=_IO, clock=clock
        ),
    )


def acceptance_refresh(args: argparse.Namespace) -> int:
    current = Path(args.current_receipt_dir)
    members = (
        *_input_members(args),
        NamedPath("free-tier-result.json", Path(args.free_tier_result)),
        *(NamedPath(name, current / name) for name in (
            "repository-scan.json", "github-public-scan.json",
            "vercel-api-inspection.json", "vercel-web-inspection.json",
            "provider-log-disposition.json", "db-binding-health.json",
        )),
    )
    request = AcceptanceRefreshRequest(
        members, Path(args.input_manifest), Path(args.current_state_manifest),
        args.expected_members, args.expected_sha, args.expected_plan_sha256,
        args.activation_nonce, Path(args.predecessor_receipt), Path(args.json_out),
    )
    return _run(
        args, lambda clock: handle_acceptance_refresh(request, io=_IO, clock=clock)
    )


HANDLERS = {
    "materialize-chain": materialize,
    "acceptance-input-manifest": acceptance_input,
    "acceptance-refresh": acceptance_refresh,
    "final-lane": final_lane,
    "final-fan-in": final_fan_in,
    "aggregate": aggregate,
}

__all__ = ("HANDLERS",)
