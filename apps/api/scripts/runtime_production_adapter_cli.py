"""Production command hook for the unified release-gate registry."""

# pyright: reportAny=false
# ruff: noqa: TC001, TC003

from __future__ import annotations

import argparse
from pathlib import Path

from .release_production import handle_production
from .release_production_models import ProductionRequest
from .release_runtime_io import BoundedPathReceiptIO
from .runtime_production_adapter import production_probe_for
from .runtime_production_adapter_http import HttpGet


def request_from_args(args: argparse.Namespace) -> ProductionRequest:
    """Convert the existing parser surface without adding credential flags."""
    return ProductionRequest(
        database_url_env=args.database_url_env,
        api_url=args.api_url,
        web_url=args.web_url,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=args.activation_nonce,
        predecessor_receipt=Path(args.predecessor_receipt),
        expected_revision=args.expected_revision,
        attestation=Path(args.attestation),
        free_tier_result=Path(args.free_tier_result),
        release_chain=Path(args.release_chain),
        json_out=Path(args.json_out),
        read_only=args.read_only,
    )


def run_production(
    args: argparse.Namespace,
    *,
    http_get: HttpGet | None = None,
) -> int:
    """Run the concrete probe and emit the canonical Production receipt."""
    request = request_from_args(args)
    probe = production_probe_for(request, http_get=http_get)
    _ = handle_production(
        request,
        io=BoundedPathReceiptIO(),
        clock=probe.clock,
        probe=probe,
    )
    return 0


HANDLERS = {"production": run_production}

__all__ = ("HANDLERS", "request_from_args", "run_production")
