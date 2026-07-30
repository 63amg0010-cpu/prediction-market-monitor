"""Injected read-only Vercel Production prestate capture."""

from __future__ import annotations

import json
from typing import cast

from scripts.release_vercel_models import (
    ChildCommand,
    ChildRunner,
    ReleaseHoldError,
    VercelPrestateRequest,
    seal_receipt,
)
from scripts.release_vercel_validation import parse_inspect, validate_identity


def run_vercel_prestate(
    request: VercelPrestateRequest,
    runner: ChildRunner,
) -> dict[str, object]:
    """Capture the currently assigned Production deployment without mutation."""
    validate_identity(request)
    command = ChildCommand(
        stage="inspect",
        argv=(
            "npx",
            "--yes",
            f"vercel@{request.cli_version}",
            "inspect",
            request.alias,
            "--scope",
            request.team_slug,
            "--json",
        ),
        cwd=request.repository_root,
        env={
            "VERCEL_ORG_ID_FROM_ENV": request.org_id_env,
            "VERCEL_PROJECT_ID_FROM_ENV": request.project_id_env,
            "VERCEL_TOKEN_FROM_ENV": request.token_env,
        },
    )
    result = runner.execute(command)
    if result.returncode:
        reason = "prestate_inspect_failed"
        raise ReleaseHoldError(reason)
    try:
        observed = cast("object", json.loads(result.stdout))
    except json.JSONDecodeError as error:
        reason = "prestate_inspect_failed_invalid_json"
        raise ReleaseHoldError(reason) from error
    if not isinstance(observed, dict):
        reason = "prestate_inspect_failed_invalid_json"
        raise ReleaseHoldError(reason)
    typed = cast("dict[str, object]", observed)
    deployment_id, url, source_sha = parse_inspect(
        typed,
        request,
        expected_source_sha=None,
    )
    return seal_receipt(
        {
            "schema_version": 1,
            "command": "vercel-prestate",
            "reviewed_sha": request.expected_sha,
            "approved_plan_sha256": request.expected_plan_sha256,
            "activation_nonce": str(request.activation_nonce),
            "project_kind": request.project_kind,
            "project_name": request.project_name,
            "team_slug": request.team_slug,
            "deployment_id": deployment_id,
            "deployment_url": url,
            "protected_source_sha": source_sha,
            "alias": request.alias,
            "accepted": True,
        }
    )


__all__ = ("run_vercel_prestate",)
