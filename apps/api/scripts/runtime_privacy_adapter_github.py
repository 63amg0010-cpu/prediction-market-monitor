"""Bounded no-shell GitHub adapter for privacy deletion and absence proof."""

# ruff: noqa: D102, D107, EM101, PLR0913, PLR2004, TC001, TC002, TC003

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final, Protocol, cast
from urllib.parse import quote

from app.domain.types import JsonValue

from scripts.release_dispatch_contracts import ChildResult
from scripts.release_privacy_contracts import (
    ArtifactTarget,
    CacheTarget,
    FrozenTarget,
    GitHubCommand,
    GitHubCommandResult,
    GitHubVerification,
    IncidentScope,
)
from scripts.release_privacy_github import REPOSITORY
from scripts.release_runtime_subprocess import DispatchRuntimeRunner, RunProcess
from scripts.runtime_privacy_adapter import (
    PrivacyProofSession,
    PrivacyRuntimeError,
    digest,
)

_ROOT: Final = f"/repos/{REPOSITORY}/actions"
_DELETE = re.compile(
    "".join(
        (
            rf"^{re.escape(_ROOT)}/(?:artifacts/[1-9][0-9]*|",
            r"runs/[1-9][0-9]*/logs|caches\?key=.+)$",
        )
    )
)
_CANCEL = re.compile(rf"^{re.escape(_ROOT)}/runs/[1-9][0-9]*/cancel$")


class Runner(Protocol):
    """Minimal existing runtime subprocess boundary."""

    def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
    ) -> ChildResult: ...


def _status(result: ChildResult, argv: tuple[str, ...]) -> str:
    return digest(
        {
            "argv_sha256": digest(argv),
            "returncode": result.returncode,
            "stderr_sha256": digest(result.stderr),
            "stdout_sha256": digest(result.stdout),
        }
    )


def _not_found(result: ChildResult) -> bool:
    return result.returncode != 0 and bool(
        re.search(r"(?:HTTP\s+404|Not Found)", result.stderr, re.IGNORECASE)
    )


def _json(result: ChildResult) -> dict[str, JsonValue]:
    if result.returncode != 0:
        raise PrivacyRuntimeError("github_read_failed")
    try:
        loaded = cast("object", json.loads(result.stdout))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise PrivacyRuntimeError("github_response_invalid") from error
    if not isinstance(loaded, dict):
        raise PrivacyRuntimeError("github_response_invalid")
    return cast("dict[str, JsonValue]", loaded)


class PrivacyGitHubAdapter:
    """Concrete ``gh api`` adapter with an allowlisted exact repository."""

    def __init__(
        self,
        runner: Runner,
        proof: PrivacyProofSession,
        scope: IncidentScope,
    ) -> None:
        self._runner: Runner = runner
        self._proof: PrivacyProofSession = proof
        self._scope: IncidentScope = scope

    @classmethod
    def from_env(
        cls,
        repository_root: Path,
        token_env: str,
        proof: PrivacyProofSession,
        scope: IncidentScope,
        *,
        repository: str = REPOSITORY,
        environ: Mapping[str, str] | None = None,
        run_process: RunProcess = subprocess.run,
        timeout_seconds: float = 30.0,
    ) -> PrivacyGitHubAdapter:
        """Validate repository and token env before any child process starts."""
        if repository != REPOSITORY:
            raise PrivacyRuntimeError("github_repository_mismatch")
        runner = DispatchRuntimeRunner(
            repository_root,
            token_env=token_env,
            timeout_seconds=timeout_seconds,
            environ=environ,
            run_process=run_process,
        )
        return cls(runner, proof, scope)

    async def execute(
        self,
        command: GitHubCommand,
    ) -> GitHubCommandResult:
        argv = command.argv
        valid = (
            len(argv) == 5
            and argv[:2] == ("gh", "api")
            and argv[2] == "--method"
            and (
                (argv[3] == "DELETE" and _DELETE.fullmatch(argv[4]))
                or (argv[3] == "POST" and _CANCEL.fullmatch(argv[4]))
            )
        )
        if not valid:
            raise PrivacyRuntimeError("github_mutation_not_allowlisted")
        result = self._runner.run(argv)
        return GitHubCommandResult(
            succeeded=result.returncode == 0 or _not_found(result),
            status_sha256=_status(result, argv),
        )

    async def verify_absent(
        self,
        targets: tuple[FrozenTarget, ...],
    ) -> GitHubVerification:
        artifacts = caches = logs = True
        observations: list[str] = []
        for target in targets:
            if isinstance(target, ArtifactTarget):
                argv = (
                    "gh",
                    "api",
                    f"{_ROOT}/artifacts/{target.artifact_id}",
                )
                result = self._runner.run(argv)
                artifacts &= _not_found(result)
            elif isinstance(target, CacheTarget):
                key = quote(target.key.get_secret_value(), safe="")
                argv = ("gh", "api", f"{_ROOT}/caches?key={key}")
                result = self._runner.run(argv)
                body = _json(result)
                entries = body.get("actions_caches")
                caches &= isinstance(entries, list) and len(entries) == 0
            else:
                argv = (
                    "gh",
                    "api",
                    f"{_ROOT}/runs/{target.run_id}/logs",
                )
                result = self._runner.run(argv)
                logs &= _not_found(result)
            observations.append(_status(result, argv))
        proof_sha = digest(observations)
        accepted = artifacts and caches and logs
        if accepted:
            self._proof.record(
                "github",
                self._scope,
                proof_sha,
                accepted=True,
            )
        return GitHubVerification(
            artifacts_absent=artifacts,
            caches_absent=caches,
            logs_return_404=logs,
            checked_target_count=len(targets),
            verification_sha256=proof_sha,
        )


__all__ = ("PrivacyGitHubAdapter",)
