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
from scripts.release_vercel_validation import (
    parse_inspect,
    parse_inspect_reference,
    validate_identity,
)


def _json_object(result_stdout: str, reason: str) -> dict[str, object]:
    try:
        observed = cast("object", json.loads(result_stdout))
    except json.JSONDecodeError as error:
        raise ReleaseHoldError(reason) from error
    if not isinstance(observed, dict):
        raise ReleaseHoldError(reason)
    return cast("dict[str, object]", observed)


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
    typed = _json_object(
        result.stdout,
        "prestate_inspect_failed_invalid_json",
    )
    summary_id, summary_url = parse_inspect_reference(typed, request)
    api_result = runner.execute(
        ChildCommand(
            stage="inspect-api",
            argv=(
                "npx",
                "--yes",
                f"vercel@{request.cli_version}",
                "api",
                f"/v13/deployments/{summary_id}",
                "--scope",
                request.team_slug,
                "--raw",
            ),
            cwd=request.repository_root,
            env={
                "VERCEL_ORG_ID_FROM_ENV": request.org_id_env,
                "VERCEL_PROJECT_ID_FROM_ENV": request.project_id_env,
                "VERCEL_TOKEN_FROM_ENV": request.token_env,
            },
        )
    )
    if api_result.returncode:
        reason = "prestate_deployment_api_failed"
        raise ReleaseHoldError(reason)
    api_observation = _json_object(
        api_result.stdout,
        "prestate_deployment_api_failed_invalid_json",
    )
    deployment_id, url, source_sha = parse_inspect(
        api_observation,
        request,
        expected_source_sha=None,
    )
    if deployment_id != summary_id or url != summary_url:
        reason = "prestate_inspect_api_mismatch"
        raise ReleaseHoldError(reason)
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
