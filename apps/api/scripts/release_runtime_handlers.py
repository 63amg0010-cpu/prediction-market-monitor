"""Single registry hook for edge-owned release runtime handlers."""

# ruff: noqa: PLR2004
# pyright: reportAny=false, reportArgumentType=false
# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import anyio

from scripts.release_chain_acceptance import NamedPath
from scripts.release_chain_evidence_leaf import (
    AcceptanceCaptureRequest,
    handle_acceptance_capture,
)
from scripts.release_gate_cli_io import write_document
from scripts.release_runtime_acceptance import ReleaseAcceptanceProvider
from scripts.release_runtime_database import (
    engine_from_named_env,
    read_only_repeatable_read,
)
from scripts.release_runtime_http import ReadOnlyHttpProbe
from scripts.release_runtime_io import BoundedPathReceiptIO
from scripts.release_runtime_prestate import capture_composite_prestate
from scripts.release_runtime_recovery import recover_ledger_receipt
from scripts.release_runtime_subprocess import (
    DispatchRuntimeRunner,
    VercelRuntimeRunner,
)
from scripts.release_vercel_models import CLI_VERSION, ChildCommand

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncConnection

Handler = Callable[[argparse.Namespace], int]
ROOT = Path(__file__).resolve().parents[3]


def _recover(args: argparse.Namespace) -> int:
    runner = DispatchRuntimeRunner(
        ROOT,
        token_env=args.github_token_env,
    )
    raw = recover_ledger_receipt(
        database_url_env=args.database_url_env,
        runner=runner,
        repository=args.repository,
        workflow=args.workflow,
        original_run_id=args.original_run_id,
        operation=args.operation,
        revision=args.revision,
        attempt=args.attempt,
        dispatch_nonce=args.dispatch_nonce,
        activation_nonce=args.activation_nonce,
        expected_head_sha=args.expected_head_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        expected_ledger_receipt_sha256=args.expected_ledger_receipt_sha256,
    )
    output = Path(args.json_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    _ = output.write_bytes(raw)
    return 0


def _prestate(args: argparse.Namespace) -> int:
    if args.predecessor_receipt != "none":
        msg = "deployment_prestate_predecessor_not_null"
        raise ValueError(msg)
    required = (
        args.org_id_env,
        args.api_project_id_env,
        args.web_project_id_env,
        args.token_env,
    )
    if any(not os.environ.get(name) for name in required):
        msg = "vercel_credential_environment_empty"
        raise ValueError(msg)
    engine = engine_from_named_env(args.database_url_env)
    try:
        receipt = capture_composite_prestate(
            repository_root=ROOT,
            engine=engine,
            review_record=Path(args.review_record),
            live_plan=Path(args.plan),
            expected_sha=args.expected_sha,
            activation_nonce=UUID(args.activation_nonce),
            team_slug=args.team_slug,
            org_id_env=args.org_id_env,
            api_project_name=args.api_project_name,
            api_project_id_env=args.api_project_id_env,
            web_project_name=args.web_project_name,
            web_project_id_env=args.web_project_id_env,
            token_env=args.token_env,
        )
    finally:
        anyio.run(engine.dispose)
    write_document(args.json_out, receipt)
    return 0


def _acceptance_inputs(args: argparse.Namespace) -> tuple[NamedPath, ...]:
    providers = tuple(Path(value) for value in args.provider_capture)
    if len(providers) != 4:
        msg = "provider_capture_count_invalid"
        raise ValueError(msg)
    return (
        NamedPath("manifold-evidence.json", Path(args.authorization_evidence)),
        NamedPath("production-free-tier.json", Path(args.provider_manifest)),
        NamedPath("free-tier-measurements.json", Path(args.local_measurements)),
        *(NamedPath(path.name, path) for path in providers),
        NamedPath(
            "production-db-measurements.json",
            Path(args.production_measurements),
        ),
    )


def _acceptance_capture(args: argparse.Namespace) -> int:
    environment_names = (
        args.github_repository_id_env,
        args.org_id_env,
        args.api_project_id_env,
        args.web_project_id_env,
        args.supabase_project_id_env,
        args.supabase_org_id_env,
        args.github_token_env,
        args.vercel_token_env,
    )
    if any(not os.environ.get(name) for name in environment_names):
        msg = "acceptance_environment_empty"
        raise ValueError(msg)
    engine = engine_from_named_env(args.database_url_env)
    github = DispatchRuntimeRunner(ROOT, token_env=args.github_token_env)
    vercel = VercelRuntimeRunner()

    async def clock_value(
        _connection: AsyncConnection,
        observed_at: datetime,
    ) -> datetime:
        return observed_at

    observed_at = anyio.run(read_only_repeatable_read, engine, clock_value)
    commands = {
        f"vercel-{kind}-inspection.json": ChildCommand(
            "acceptance-inspect",
            (
                "npx",
                "--yes",
                f"vercel@{CLI_VERSION}",
                "inspect",
                f"{name}.vercel.app",
                "--scope",
                args.team_slug,
                "--json",
            ),
            ROOT,
            {
                "VERCEL_ORG_ID_FROM_ENV": args.org_id_env,
                "VERCEL_PROJECT_ID_FROM_ENV": project_env,
                "VERCEL_TOKEN_FROM_ENV": args.vercel_token_env,
            },
        )
        for kind, name, project_env in (
            ("api", args.api_project_name, args.api_project_id_env),
            ("web", args.web_project_name, args.web_project_id_env),
        )
    }
    provider = ReleaseAcceptanceProvider(
        repository_root=ROOT,
        repository=args.repository,
        expected_sha=args.expected_sha,
        engine=engine,
        github=github,
        vercel=vercel,
        vercel_commands=commands,
        api_url=args.api_url,
        web_url=args.web_url,
        http_fetch=ReadOnlyHttpProbe().fetch,
        clock=lambda: observed_at,
    )
    request = AcceptanceCaptureRequest(
        inputs=_acceptance_inputs(args),
        input_manifest=Path(args.input_manifest),
        free_tier_result=Path(args.free_tier_result),
        output_dir=Path(args.output_dir),
        current_state_out=Path(args.json_out),
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=args.activation_nonce,
        predecessor_receipt=Path(args.predecessor_receipt),
    )
    try:
        _ = handle_acceptance_capture(
            request,
            io=BoundedPathReceiptIO(),
            clock=lambda: observed_at,
            provider=provider,
        )
    finally:
        anyio.run(engine.dispose)
    return 0


def runtime_handlers(
    additional: Mapping[str, Handler] | None = None,
) -> dict[str, Handler]:
    """Return concrete edge handlers plus caller-owned transaction handlers."""
    return {
        "acceptance-capture": _acceptance_capture,
        "recover-operation-receipt": _recover,
        "vercel-prestate": _prestate,
        **dict(additional or {}),
    }


__all__ = ("Handler", "runtime_handlers")
