"""Exact-argv, no-shell child adapters with bounded captured output."""

# ruff: noqa: D102, D107, EM101

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import final

from scripts.release_dispatch_contracts import ChildResult as DispatchResult
from scripts.release_vercel_models import (
    ChildCommand,
)
from scripts.release_vercel_models import (
    ChildResult as VercelResult,
)

_MAX_OUTPUT = 262_144


def _bounded(value: str) -> str:
    if len(value.encode("utf-8")) > _MAX_OUTPUT:
        raise ValueError("child_output_too_large")
    return value


@final
class DispatchSubprocessRunner:
    """Run a GitHub CLI child exactly once without a shell."""

    def __init__(self, token_env: str | None = None) -> None:
        self._environment: dict[str, str] = os.environ.copy()
        if token_env is not None:
            token = os.environ.get(token_env)
            if not token:
                raise ValueError("github_token_environment_empty")
            self._environment["GH_TOKEN"] = token

    def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
    ) -> DispatchResult:
        completed = subprocess.run(  # noqa: S603
            argv,
            cwd=Path(__file__).resolve().parents[3],
            env=self._environment,
            input=stdin,
            capture_output=True,
            check=False,
        )
        return DispatchResult(
            completed.returncode,
            _bounded(completed.stdout.decode("utf-8", errors="strict")),
            _bounded(completed.stderr.decode("utf-8", errors="replace")),
        )


@final
class VercelSubprocessRunner:
    """Resolve named credentials and run one declared Vercel child."""

    def execute(self, command: ChildCommand) -> VercelResult:
        environment = os.environ.copy()
        for target, source_name in command.env.items():
            value = os.environ.get(source_name)
            if not value:
                raise ValueError("vercel_credential_environment_empty")
            environment[target.removesuffix("_FROM_ENV")] = value
        completed = subprocess.run(  # noqa: S603
            command.argv,
            cwd=command.cwd,
            env=environment,
            capture_output=True,
            check=False,
        )
        return VercelResult(
            completed.returncode,
            _bounded(completed.stdout.decode("utf-8", errors="strict")),
            _bounded(completed.stderr.decode("utf-8", errors="replace")),
        )


__all__ = ("DispatchSubprocessRunner", "VercelSubprocessRunner")
