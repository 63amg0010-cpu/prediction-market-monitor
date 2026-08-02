"""Fail-closed validation for Vercel release observations."""

# ruff: noqa: D103, EM101

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from scripts.release_vercel_models import (
    CLI_VERSION,
    ORG_ID_ENV,
    PROJECTS,
    TEAM_SLUG,
    TOKEN_ENV,
    ReleaseHoldError,
    VercelOperation,
    VercelPrestateRequest,
)

GIT_SHA_LENGTH = 40
SHA256_LENGTH = 64


def _sha(value: str, reason: str) -> None:
    if len(value) != GIT_SHA_LENGTH or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise ReleaseHoldError(reason)


def validate_identity(
    request: VercelOperation | VercelPrestateRequest,
) -> None:
    expected_name, expected_id_env, _ = PROJECTS[request.project_kind]
    if request.team_slug != TEAM_SLUG:
        raise ReleaseHoldError("wrong_team")
    if request.project_name != expected_name:
        raise ReleaseHoldError("wrong_project")
    if request.project_id_env != expected_id_env:
        raise ReleaseHoldError("wrong_project_id_env")
    if request.org_id_env != ORG_ID_ENV or request.token_env != TOKEN_ENV:
        raise ReleaseHoldError("wrong_vercel_env_name")
    if request.cli_version != CLI_VERSION:
        raise ReleaseHoldError("unpinned_vercel_cli")
    if request.protected_ref != "origin/main":
        raise ReleaseHoldError("wrong_protected_ref")
    _sha(request.expected_sha, "invalid_expected_sha")
    if len(request.expected_plan_sha256) != SHA256_LENGTH:
        raise ReleaseHoldError("invalid_plan_sha")


def validate_operation(request: VercelOperation) -> None:
    validate_identity(request)
    _validate_operation_receipts(request)
    if (
        request.operation != "split-compensation"
        and request.target_sha != request.expected_sha
    ):
        raise ReleaseHoldError("wrong_target_sha")
    if request.environment != "production":
        raise ReleaseHoldError("wrong_environment")
    if request.attempt_root.name != f"attempt-{request.attempt}":
        raise ReleaseHoldError("flat_attempt_output")
    if request.attempt_root.parent.name != request.project_kind:
        raise ReleaseHoldError("wrong_attempt_project_root")
    if request.attempt_root.parent.parent.name != request.operation:
        raise ReleaseHoldError("wrong_operation_attempt_root")
    _validate_attempt_predecessor(request)


def _validate_operation_receipts(request: VercelOperation) -> None:
    if (
        request.operation != "split-compensation"
        and request.deployment_prestate is not None
    ):
        raise ReleaseHoldError("unexpected_deployment_prestate")
    if (
        request.operation != "compat-alias"
        and request.target_deployment_receipt is not None
    ):
        raise ReleaseHoldError("unexpected_target_deployment_receipt")
    if request.operation == "split-compensation":
        _validate_split_prestate(request)
    if request.operation == "compat-alias":
        _validate_alias_target(request)


def _validate_split_prestate(request: VercelOperation) -> None:
    prestate = request.deployment_prestate
    if (
        prestate is None
        or prestate.get("command") != "vercel-prestate"
        or prestate.get("project_kind") != request.project_kind
        or prestate.get("protected_source_sha") != request.target_sha
        or prestate.get("accepted") is not True
    ):
        raise ReleaseHoldError("wrong_split_prestate")


def _validate_alias_target(request: VercelOperation) -> None:
    target = request.target_deployment_receipt
    if (
        target is None
        or target.get("command") != "vercel-deploy"
        or target.get("operation") != "initial-deploy"
        or target.get("project_kind") != request.project_kind
        or target.get("deployment_id") in (None, "")
        or target.get("deployment_url") in (None, "")
        or target.get("source_sha") != request.expected_sha
        or target.get("ready_state") != "READY"
        or target.get("environment") != "production"
        or target.get("accepted") is not True
    ):
        raise ReleaseHoldError("invalid_compat_alias_target")


def _validate_attempt_predecessor(request: VercelOperation) -> None:
    predecessor = request.predecessor_receipt
    if request.attempt == 1:
        if predecessor.get("accepted") is not True:
            raise ReleaseHoldError("missing_predecessor")
    elif (
        predecessor.get("attempt") != 1
        or predecessor.get("accepted") is not False
        or predecessor.get("terminal_for_attempt") is not True
        or predecessor.get("retry_permitted") is not True
        or predecessor.get("operation") != request.operation
        or predecessor.get("project_kind") != request.project_kind
    ):
        raise ReleaseHoldError("illegal_attempt_2_predecessor")


def parse_inspect_reference(
    observation: Mapping[str, object],
    request: VercelOperation | VercelPrestateRequest,
) -> tuple[str, str]:
    """Validate the identity and state shared by inspect and deployment API."""
    deployment_id = observation.get("id")
    url = observation.get("url")
    if not isinstance(deployment_id, str) or not deployment_id:
        raise ReleaseHoldError("missing_deployment_id")
    if not isinstance(url, str) or not url.endswith(".vercel.app"):
        raise ReleaseHoldError("invalid_deployment_url")
    if observation.get("name") != request.project_name:
        raise ReleaseHoldError("inspect_wrong_project")
    team = observation.get("team")
    team_slug = (
        cast("Mapping[str, object]", team).get("slug")
        if isinstance(team, Mapping)
        else team
    )
    if team_slug is None:
        team_slug = observation.get("contextName")
    if team_slug != request.team_slug:
        raise ReleaseHoldError("inspect_wrong_team")
    if observation.get("target") != "production":
        raise ReleaseHoldError("not_production")
    if observation.get("readyState") != "READY":
        raise ReleaseHoldError("deployment_not_ready")
    return deployment_id, url


def parse_inspect(
    observation: Mapping[str, object],
    request: VercelOperation | VercelPrestateRequest,
    *,
    expected_source_sha: str | None,
) -> tuple[str, str, str]:
    deployment_id, url = parse_inspect_reference(observation, request)
    meta = observation.get("meta")
    if not isinstance(meta, Mapping):
        raise ReleaseHoldError("missing_source_metadata")
    typed_meta = cast("Mapping[str, object]", meta)
    source_sha = typed_meta.get("githubCommitSha")
    if not isinstance(source_sha, str):
        raise ReleaseHoldError("missing_source_sha")
    _sha(source_sha, "invalid_source_sha")
    if expected_source_sha is not None and source_sha != expected_source_sha:
        raise ReleaseHoldError("inspect_wrong_sha")
    return deployment_id, url, source_sha


def validate_health(
    observation: Mapping[str, object],
    *,
    expected_sha: str,
) -> None:
    if observation.get("status") != "ok":
        raise ReleaseHoldError("health_failed")
    if observation.get("reviewed_sha") != expected_sha:
        raise ReleaseHoldError("health_wrong_sha")
    if observation.get("database_revision") != "20260727_0010":
        raise ReleaseHoldError("health_wrong_database_revision")
    if observation.get("manifold_enabled") is not False:
        raise ReleaseHoldError("health_manifold_not_inert")


def validate_alias_listing(
    observation: Mapping[str, object],
    *,
    expected_alias: str,
    expected_deployment_id: str,
) -> None:
    """Require one exact alias-to-deployment resolution."""
    aliases = observation.get("aliases")
    if not isinstance(aliases, list):
        raise ReleaseHoldError("alias_listing_invalid")
    match_count = 0
    for item in cast("list[object]", aliases):
        if not isinstance(item, dict):
            continue
        typed_item = cast("dict[str, object]", item)
        if (
            typed_item.get("alias") == expected_alias
            and typed_item.get("deploymentId") == expected_deployment_id
        ):
            match_count += 1
    if match_count != 1:
        raise ReleaseHoldError("alias_resolution_mismatch")


__all__ = (
    "parse_inspect",
    "parse_inspect_reference",
    "validate_alias_listing",
    "validate_health",
    "validate_identity",
    "validate_operation",
)
