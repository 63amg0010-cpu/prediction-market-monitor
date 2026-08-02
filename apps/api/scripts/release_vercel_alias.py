"""Injected Vercel compatibility-alias attempt handler."""

# ruff: noqa: C901, EM101, PLR0911

from __future__ import annotations

import json
from typing import cast

from scripts.release_vercel_models import (
    ChildCommand,
    ChildRunner,
    ReleaseHoldError,
    VercelOperation,
    verify_failed_receipt,
    verify_receipt,
)
from scripts.release_vercel_receipts import accepted_attempt, failed_attempt
from scripts.release_vercel_validation import (
    parse_inspect,
    parse_inspect_reference,
    validate_alias_listing,
    validate_operation,
)


def _json(raw: str) -> dict[str, object] | None:
    try:
        value = cast("object", json.loads(raw))
    except json.JSONDecodeError:
        return None
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def run_compat_alias(
    request: VercelOperation,
    runner: ChildRunner,
) -> dict[str, object]:
    """Assign and prove one dedicated retained compatibility alias."""
    if request.operation != "compat-alias":
        raise ReleaseHoldError("compat_alias_wrong_operation")
    validate_operation(request)
    verify_predecessor = (
        verify_receipt if request.attempt == 1 else verify_failed_receipt
    )
    predecessor_sha = verify_predecessor(
        request.predecessor_receipt,
        expected_sha=request.expected_sha,
        expected_plan_sha256=request.expected_plan_sha256,
        activation_nonce=request.activation_nonce,
    )
    target = request.target_deployment_receipt
    if target is None:
        raise ReleaseHoldError("missing_compat_alias_target")
    _ = verify_receipt(
        target,
        expected_sha=request.expected_sha,
        expected_plan_sha256=request.expected_plan_sha256,
        activation_nonce=request.activation_nonce,
    )
    deployment_id = str(target["deployment_id"])
    deployment_url = str(target["deployment_url"])
    base = ("npx", "--yes", f"vercel@{request.cli_version}")
    environment = {
        "VERCEL_ORG_ID_FROM_ENV": request.org_id_env,
        "VERCEL_PROJECT_ID_FROM_ENV": request.project_id_env,
        "VERCEL_TOKEN_FROM_ENV": request.token_env,
    }
    stages: list[str] = []
    commands = (
        ChildCommand(
            "alias",
            (
                *base,
                "alias",
                "set",
                deployment_url,
                request.alias,
                "--scope",
                request.team_slug,
            ),
            request.repository_root,
            environment,
        ),
        ChildCommand(
            "alias-ls",
            (*base, "alias", "ls", "--scope", request.team_slug, "--json"),
            request.repository_root,
            environment,
        ),
        ChildCommand(
            "inspect",
            (
                *base,
                "inspect",
                request.alias,
                "--scope",
                request.team_slug,
                "--json",
            ),
            request.repository_root,
            environment,
        ),
    )
    observations: dict[str, dict[str, object]] = {}
    for command in commands:
        result = runner.execute(command)
        stages.append(command.stage)
        if result.returncode:
            return failed_attempt(
                request,
                predecessor_sha,
                command.stage,
                stages,
            )
        if command.stage != "alias":
            observed = _json(result.stdout)
            if observed is None:
                return failed_attempt(
                    request,
                    predecessor_sha,
                    command.stage,
                    stages,
                )
            observations[command.stage] = observed
    try:
        validate_alias_listing(
            observations["alias-ls"],
            expected_alias=request.alias,
            expected_deployment_id=deployment_id,
        )
        summary_id, summary_url = parse_inspect_reference(
            observations["inspect"], request
        )
    except ReleaseHoldError:
        return failed_attempt(request, predecessor_sha, "verification", stages)
    inspect_api_command = ChildCommand(
        "inspect-api",
        (
            *base,
            "api",
            f"/v13/deployments/{summary_id}",
            "--scope",
            request.team_slug,
            "--raw",
        ),
        request.repository_root,
        environment,
    )
    inspect_api_result = runner.execute(inspect_api_command)
    stages.append(inspect_api_command.stage)
    inspect_api = _json(inspect_api_result.stdout)
    if inspect_api_result.returncode or inspect_api is None:
        return failed_attempt(request, predecessor_sha, "inspect-api", stages)
    try:
        inspected_id, _, _ = parse_inspect(
            inspect_api,
            request,
            expected_source_sha=request.expected_sha,
        )
    except ReleaseHoldError:
        return failed_attempt(request, predecessor_sha, "verification", stages)
    if (
        inspected_id != deployment_id
        or inspected_id != summary_id
        or summary_url != deployment_url
    ):
        return failed_attempt(request, predecessor_sha, "verification", stages)
    return accepted_attempt(
        request,
        predecessor_sha,
        deployment_id,
        deployment_url,
        stages,
    )


__all__ = ("run_compat_alias",)
