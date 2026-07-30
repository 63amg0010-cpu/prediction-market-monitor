"""Exact-attempt Vercel deployment and rebuild adapters."""

# pyright: reportAny=false, reportArgumentType=false
# ruff: noqa: TC003

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import cast
from uuid import UUID

from scripts.release_gate_cli_io import read_document, write_document
from scripts.release_gate_cli_subprocess import VercelSubprocessRunner
from scripts.release_vercel_handlers import run_vercel_deploy, run_vercel_restore
from scripts.release_vercel_models import VercelOperation


def _auxiliary_receipts(
    args: argparse.Namespace,
) -> tuple[Mapping[str, object] | None, Mapping[str, object] | None]:
    prestate_path = getattr(args, "deployment_prestate", None)
    target_path = getattr(args, "target_deployment_receipt", None)
    requires_prestate = args.command == "vercel-restore" and (
        args.operation == "split-compensation"
    )
    requires_target = args.command == "vercel-deploy" and (
        args.operation == "compat-alias"
    )
    if (prestate_path is not None) != requires_prestate:
        message = "vercel_deployment_prestate_contract_invalid"
        raise ValueError(message)
    if (target_path is not None) != requires_target:
        message = "vercel_target_deployment_contract_invalid"
        raise ValueError(message)
    return (
        None if prestate_path is None else read_document(prestate_path),
        None if target_path is None else read_document(target_path),
    )


def _request(args: argparse.Namespace) -> VercelOperation:
    target = args.target_sha or args.expected_sha
    deployment_prestate, target_deployment_receipt = _auxiliary_receipts(args)
    return VercelOperation(
        operation=cast("object", args.operation),
        attempt=cast("object", args.attempt),
        attempt_root=Path(args.attempt_root),
        repository_root=Path(__file__).resolve().parents[3],
        project_kind=cast("object", args.project_kind),
        team_slug=args.team_slug,
        org_id_env=args.org_id_env,
        project_name=args.project_name,
        project_id_env=args.project_id_env,
        token_env=args.token_env,
        target_sha=target,
        protected_ref=args.protected_ref,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
        cli_version=args.cli_version,
        predecessor_receipt=read_document(args.predecessor_receipt),
        deployment_prestate=deployment_prestate,
        target_deployment_receipt=target_deployment_receipt,
    )


def deploy(args: argparse.Namespace) -> int:
    receipt = run_vercel_deploy(_request(args), VercelSubprocessRunner())
    write_document(args.json_out, receipt)
    return 0


def restore(args: argparse.Namespace) -> int:
    receipt = run_vercel_restore(_request(args), VercelSubprocessRunner())
    write_document(args.json_out, receipt)
    return 0


HANDLERS = {"vercel-deploy": deploy, "vercel-restore": restore}

__all__ = ("HANDLERS",)
