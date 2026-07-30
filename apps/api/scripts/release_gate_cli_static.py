"""Adapters for local database and static repository gates."""

# pyright: reportAny=false, reportArgumentType=false
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false
# ruff: noqa: TC003

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.local_db_gate import request_from_namespace, run_local_db
from scripts.release_static_gates import (
    CodeQualityRequest,
    LinksRequest,
    PlanComplianceRequest,
    ScopeFidelityRequest,
    SecretScanRequest,
    run_code_quality,
    run_links,
    run_plan_compliance,
    run_scope_fidelity,
    run_secret_static_scan,
)


def _path(args: argparse.Namespace, name: str) -> Path | None:
    value = getattr(args, name, None)
    return None if value is None else Path(str(value))


def local_db(args: argparse.Namespace) -> int:
    return run_local_db(request_from_namespace(args))


def secret_scan(args: argparse.Namespace) -> int:
    return run_secret_static_scan(
        SecretScanRequest(
            Path(args.root), args.base_sha, args.reviewed_sha, Path(args.json_out)
        )
    )


def code_quality(args: argparse.Namespace) -> int:
    return run_code_quality(
        CodeQualityRequest(
            Path(),
            args.base_sha,
            args.reviewed_sha,
            Path(args.evidence_dir),
            Path(args.output),
        )
    )


def plan_compliance(args: argparse.Namespace) -> int:
    return run_plan_compliance(
        PlanComplianceRequest(
            root=Path(args.root or "."),
            json_out=_path(args, "json_out"),
            plan=_path(args, "plan"),
            base_sha=args.base_sha,
            reviewed_sha=args.reviewed_sha,
            evidence_dir=_path(args, "evidence_dir"),
            production_result=_path(args, "production_result"),
            expected_revision=args.expected_revision,
            output=_path(args, "output"),
        )
    )


def scope_fidelity(args: argparse.Namespace) -> int:
    return run_scope_fidelity(
        ScopeFidelityRequest(
            root=Path(args.root or "."),
            json_out=_path(args, "json_out"),
            plan=_path(args, "plan"),
            base_sha=args.base_sha,
            reviewed_sha=args.reviewed_sha,
            evidence_dir=_path(args, "evidence_dir"),
            production_result=_path(args, "production_result"),
            fan_in=_path(args, "fan_in"),
            cadence=_path(args, "cadence"),
            acceptance_refresh=_path(args, "acceptance_refresh"),
            expected_sha=args.expected_sha,
            expected_plan_sha256=args.expected_plan_sha256,
            activation_nonce=args.activation_nonce,
            predecessor_receipt=_path(args, "predecessor_receipt"),
            output=_path(args, "output"),
        )
    )


def links(args: argparse.Namespace) -> int:
    values = args.paths
    paths = tuple(values) if isinstance(values, list) else (str(values),)
    return run_links(LinksRequest(Path(args.root), paths, Path(args.json_out)))


HANDLERS = {
    "local-db": local_db,
    "code-quality": code_quality,
    "secret-static-scan": secret_scan,
    "plan-compliance": plan_compliance,
    "scope-fidelity": scope_fidelity,
    "links": links,
}

__all__ = ("HANDLERS",)
