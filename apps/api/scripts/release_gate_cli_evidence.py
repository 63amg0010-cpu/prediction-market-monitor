"""Filesystem-only evidence graph adapters for the unified CLI."""

# pyright: reportAny=false, reportArgumentType=false
# ruff: noqa: TC003

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import UUID

from scripts.release_evidence_graph import (
    canonical_hash,
    evidence_import,
    evidence_join,
)
from scripts.release_gate_cli_io import (
    csv,
    read_document,
    strings,
    write_document,
)


def run_canonical_hash(args: argparse.Namespace) -> int:
    receipt = canonical_hash(read_document(args.input))
    write_document(args.json_out, receipt)
    return 0


def run_evidence_import(args: argparse.Namespace) -> int:
    receipt = evidence_import(
        kind=args.kind,
        document=read_document(args.input),
        expected_input_sha256=args.expected_input_sha256,
        output_path=Path(args.json_out),
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
        predecessor_receipt=read_document(args.predecessor_receipt),
    )
    write_document(args.json_out, receipt)
    return 0


def run_evidence_join(args: argparse.Namespace) -> int:
    receipt = evidence_join(
        deployment_root=read_document(args.deployment_root),
        branches=tuple(read_document(path) for path in strings(args.branch)),
        expected_branches=csv(args.expected_branches),
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
        predecessor_receipt=read_document(args.predecessor_receipt),
    )
    write_document(args.json_out, receipt)
    return 0


HANDLERS = {
    "canonical-hash": run_canonical_hash,
    "evidence-import": run_evidence_import,
    "evidence-join": run_evidence_join,
}

__all__ = ("HANDLERS",)
