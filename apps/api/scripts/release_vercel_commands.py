"""Injected-child handlers for exact-SHA Vercel release operations."""

# ruff: noqa: C901, D103, EM101, EM102, PLR0911, PLR0912, PLR0915, TC003

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from scripts.release_vercel_models import (
    ChildCommand,
    ChildResult,
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
    validate_health,
    validate_operation,
)


def _run(
    runner: ChildRunner,
    stage: str,
    argv: tuple[str, ...],
    cwd: Path,
    env: Mapping[str, str],
) -> ChildResult:
    return runner.execute(ChildCommand(stage, argv, cwd, env))


def _json(result: ChildResult, reason: str) -> Mapping[str, object]:
    if result.returncode:
        raise ReleaseHoldError(reason)
    try:
        value = cast("object", json.loads(result.stdout))
    except json.JSONDecodeError as error:
        raise ReleaseHoldError(f"{reason}_invalid_json") from error
    if not isinstance(value, dict):
        raise ReleaseHoldError(f"{reason}_invalid_json")
    return cast("dict[str, object]", value)


def _env(
    org_id_env: str,
    project_id_env: str,
    token_env: str,
) -> dict[str, str]:
    return {
        "VERCEL_ORG_ID_FROM_ENV": org_id_env,
        "VERCEL_PROJECT_ID_FROM_ENV": project_id_env,
        "VERCEL_TOKEN_FROM_ENV": token_env,
    }


def run_vercel_operation(
    request: VercelOperation,
    runner: ChildRunner,
) -> dict[str, object]:
    if request.operation == "compat-alias":
        raise ReleaseHoldError("compat_alias_requires_alias_handler")
    validate_operation(request)
    verify = verify_receipt if request.attempt == 1 else verify_failed_receipt
    predecessor_sha = verify(
        request.predecessor_receipt,
        expected_sha=request.expected_sha,
        expected_plan_sha256=request.expected_plan_sha256,
        activation_nonce=request.activation_nonce,
    )
    if request.deployment_prestate is not None:
        _ = verify_receipt(
            request.deployment_prestate,
            expected_sha=request.expected_sha,
            expected_plan_sha256=request.expected_plan_sha256,
            activation_nonce=request.activation_nonce,
        )
    worktree = request.attempt_root / "worktree"
    environment = _env(
        request.org_id_env,
        request.project_id_env,
        request.token_env,
    )
    base = ("npx", "--yes", f"vercel@{request.cli_version}")
    stages: list[str] = []
    attached = False
    try:
        checks = (
            (
                "target-sha",
                ("git", "rev-parse", "--verify", f"{request.target_sha}^{{commit}}"),
            ),
            (
                "protected-sha",
                ("git", "rev-parse", "--verify", f"{request.protected_ref}^{{commit}}"),
            ),
            (
                "reachable",
                (
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    request.target_sha,
                    request.protected_ref,
                ),
            ),
        )
        for stage, argv in checks:
            result = _run(runner, stage, argv, request.repository_root, {})
            stages.append(stage)
            if result.returncode:
                raise ReleaseHoldError("target_sha_unreachable")
            expected_observed_sha = (
                request.target_sha if stage == "target-sha" else request.expected_sha
            )
            if stage != "reachable" and result.stdout.strip() != expected_observed_sha:
                raise ReleaseHoldError("reviewed_sha_not_protected_head")
        add = _run(
            runner,
            "worktree-add",
            ("git", "worktree", "add", "--detach", str(worktree), request.target_sha),
            request.repository_root,
            {},
        )
        stages.append("worktree-add")
        if add.returncode:
            raise ReleaseHoldError("worktree_add_failed")
        attached = True
        cwd = worktree / request.project_root
        fixed = ("--scope", request.team_slug, "--yes")
        invocations = (
            ("pull", (*base, "pull", "--environment=production", *fixed)),
            ("build", (*base, "build", "--prod", *fixed)),
            ("deploy", (*base, "deploy", "--prebuilt", "--prod", *fixed)),
        )
        deployment_url = ""
        for stage, argv in invocations:
            result = _run(runner, stage, argv, cwd, environment)
            stages.append(stage)
            if result.returncode:
                return failed_attempt(request, predecessor_sha, stage, stages)
            if stage == "deploy":
                deployment_url = result.stdout.strip()
        if not deployment_url.endswith(".vercel.app"):
            return failed_attempt(
                request,
                predecessor_sha,
                "deploy-output",
                stages,
            )
        inspect_result = _run(
            runner,
            "inspect",
            (
                *base,
                "inspect",
                deployment_url,
                "--scope",
                request.team_slug,
                "--json",
            ),
            cwd,
            environment,
        )
        stages.append("inspect")
        try:
            inspect_summary = _json(inspect_result, "inspect_failed")
            summary_id, summary_url = parse_inspect_reference(inspect_summary, request)
        except ReleaseHoldError:
            return failed_attempt(request, predecessor_sha, "inspect", stages)
        inspect_api_result = _run(
            runner,
            "inspect-api",
            (
                *base,
                "api",
                f"/v13/deployments/{summary_id}",
                "--scope",
                request.team_slug,
                "--raw",
            ),
            cwd,
            environment,
        )
        stages.append("inspect-api")
        try:
            inspect_api = _json(inspect_api_result, "inspect_api_failed")
            deployment_id, inspected_url, _ = parse_inspect(
                inspect_api,
                request,
                expected_source_sha=request.target_sha,
            )
        except ReleaseHoldError:
            return failed_attempt(request, predecessor_sha, "inspect-api", stages)
        if deployment_id != summary_id or inspected_url != summary_url:
            return failed_attempt(request, predecessor_sha, "inspect-api", stages)
        alias = _run(
            runner,
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
            cwd,
            environment,
        )
        stages.append("alias")
        if alias.returncode:
            return failed_attempt(request, predecessor_sha, "alias", stages)
        health_result = _run(
            runner,
            "health",
            (
                *base,
                "curl",
                f"https://{request.alias}/health",
                "--scope",
                request.team_slug,
            ),
            cwd,
            environment,
        )
        stages.append("health")
        try:
            health = _json(health_result, "health_failed")
            validate_health(health, expected_sha=request.target_sha)
        except ReleaseHoldError:
            return failed_attempt(request, predecessor_sha, "health", stages)
        return accepted_attempt(
            request,
            predecessor_sha,
            deployment_id,
            deployment_url,
            stages,
        )
    finally:
        if attached:
            _ = _run(
                runner,
                "worktree-remove",
                ("git", "worktree", "remove", "--force", str(worktree)),
                request.repository_root,
                {},
            )


__all__ = ("run_vercel_operation",)
