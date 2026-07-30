"""Exact GitHub deletion intents for one frozen privacy graph."""

from __future__ import annotations

import hashlib
import json
from typing import Final
from urllib.parse import quote

from scripts.release_privacy_contracts import (
    ArtifactTarget,
    FrozenTarget,
    GitHubCommand,
    PrivacyGitHub,
    WorkflowTarget,
)

REPOSITORY: Final = "63amg0010-cpu/prediction-market-monitor"
RUNNING: Final = frozenset(
    {"queued", "in_progress", "waiting", "pending", "requested"}
)


class PrivacyHoldError(RuntimeError):
    """Fail closed without exposing the affected identifier."""

    def __init__(self) -> None:
        """Create the one redacted deletion failure."""
        super().__init__("PRIVACY_HOLD: github deletion failed")


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def target_sha256(target: FrozenTarget) -> str:
    """Hash a protected target without returning its raw identifier."""
    if isinstance(target, ArtifactTarget):
        protected = {"kind": target.kind, "id": target.artifact_id}
    elif isinstance(target, WorkflowTarget):
        protected = {
            "kind": target.kind,
            "id": target.run_id,
            "status": target.status,
        }
    else:
        protected = {
            "kind": target.kind,
            "key": target.key.get_secret_value(),
        }
    return hashlib.sha256(_canonical(protected)).hexdigest()


def frozen_graph_sha256(targets: tuple[FrozenTarget, ...]) -> str:
    """Bind target order and membership using one public-safe hash."""
    hashes = [target_sha256(target) for target in targets]
    return hashlib.sha256(_canonical(hashes)).hexdigest()


def deletion_commands(target: FrozenTarget) -> tuple[GitHubCommand, ...]:
    """Render the plan's exact token-free, repository-scoped argv."""
    root = f"/repos/{REPOSITORY}"
    if isinstance(target, ArtifactTarget):
        return (
            GitHubCommand(
                argv=(
                    "gh",
                    "api",
                    "--method",
                    "DELETE",
                    f"{root}/actions/artifacts/{target.artifact_id}",
                )
            ),
        )
    if isinstance(target, WorkflowTarget):
        commands: list[GitHubCommand] = []
        if target.status in RUNNING:
            commands.append(
                GitHubCommand(
                    argv=(
                        "gh",
                        "api",
                        "--method",
                        "POST",
                        f"{root}/actions/runs/{target.run_id}/cancel",
                    )
                )
            )
        commands.append(
            GitHubCommand(
                argv=(
                    "gh",
                    "api",
                    "--method",
                    "DELETE",
                    f"{root}/actions/runs/{target.run_id}/logs",
                )
            )
        )
        return tuple(commands)
    encoded = quote(target.key.get_secret_value(), safe="")
    return (
        GitHubCommand(
            argv=(
                "gh",
                "api",
                "--method",
                "DELETE",
                f"{root}/actions/caches?key={encoded}",
            )
        ),
    )


async def purge_github(
    github: PrivacyGitHub,
    targets: tuple[FrozenTarget, ...],
) -> tuple[str, ...]:
    """Cancel active runs, then delete every frozen public surface."""
    result_hashes: list[str] = []
    for target in targets:
        for command in deletion_commands(target):
            result = await github.execute(command)
            if not result.succeeded:
                raise PrivacyHoldError
            result_hashes.append(result.status_sha256)
    return tuple(result_hashes)


__all__ = (
    "PrivacyHoldError",
    "deletion_commands",
    "frozen_graph_sha256",
    "purge_github",
    "target_sha256",
)
