"""Receipt constructors for one Vercel logical attempt."""

# ruff: noqa: D103

from __future__ import annotations

from scripts.release_vercel_models import VercelOperation, seal_receipt


def failed_attempt(
    request: VercelOperation,
    predecessor_sha: str,
    failed_stage: str,
    stages: list[str],
) -> dict[str, object]:
    return seal_receipt(
        {
            **_base(request, predecessor_sha, stages),
            "accepted": False,
            "failed_stage": failed_stage,
            "terminal_for_attempt": True,
            "retry_permitted": request.attempt == 1,
            "state_after": _state_after(request),
        }
    )


def accepted_attempt(
    request: VercelOperation,
    predecessor_sha: str,
    deployment_id: str,
    deployment_url: str,
    stages: list[str],
) -> dict[str, object]:
    return seal_receipt(
        {
            **_base(request, predecessor_sha, stages),
            "accepted": True,
            "deployment_id": deployment_id,
            "deployment_url": deployment_url,
            "source_sha": request.target_sha,
            "ready_state": "READY",
            "environment": "production",
            "state_after": _state_after(request),
        }
    )


def _base(
    request: VercelOperation,
    predecessor_sha: str,
    stages: list[str],
) -> dict[str, object]:
    command = (
        "vercel-deploy"
        if request.operation in {"initial-deploy", "compat-alias"}
        else "vercel-restore"
    )
    return {
        "schema_version": 1,
        "command": command,
        "operation": request.operation,
        "attempt": request.attempt,
        "reviewed_sha": request.expected_sha,
        "approved_plan_sha256": request.expected_plan_sha256,
        "activation_nonce": str(request.activation_nonce),
        "predecessor_receipt_sha256": predecessor_sha,
        "project_kind": request.project_kind,
        "project_name": request.project_name,
        "team_slug": request.team_slug,
        "alias": request.alias,
        "invoked_stages": stages,
    }


def _state_after(request: VercelOperation) -> str:
    if request.operation == "matrix-b-rebuild":
        return "restore_writing"
    if request.operation == "split-compensation":
        return "deployment_prestate_restored"
    return "compatibility_deploying"


__all__ = ("accepted_attempt", "failed_attempt")
