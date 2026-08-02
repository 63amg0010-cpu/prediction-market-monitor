"""Fail-closed subprocess adapters for release runtime protocols."""

# ruff: noqa: D102, D107
# pyright: reportUnannotatedClassAttribute=false

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Final

from scripts.release_dispatch_contracts import ChildResult as DispatchResult
from scripts.release_vercel_models import (
    ChildCommand,
)
from scripts.release_vercel_models import (
    ChildResult as VercelResult,
)

if TYPE_CHECKING:
    from pathlib import Path

MAX_OUTPUT_BYTES: Final = 262_144
DEFAULT_TIMEOUT_SECONDS: Final = 30.0
RunProcess = Callable[..., subprocess.CompletedProcess[bytes]]
ExecutionPlatform = str


class RuntimeAdapterError(RuntimeError):
    """Stable error whose message never contains transported values."""


def _required_environment(
    names: tuple[str, ...],
    environ: Mapping[str, str],
    code: str,
) -> dict[str, str]:
    if any(not name for name in names):
        raise RuntimeAdapterError(code)
    values = {name: environ.get(name, "") for name in names}
    if any(not value for value in values.values()):
        raise RuntimeAdapterError(code)
    return values


def _safe_output(raw: bytes, secrets: tuple[str, ...]) -> str:
    if len(raw) > MAX_OUTPUT_BYTES:
        msg = "child_output_too_large"
        raise RuntimeAdapterError(msg)
    value = raw.decode("utf-8", errors="replace")
    for secret in secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def _validate_argv(argv: tuple[str, ...], secrets: tuple[str, ...]) -> None:
    if not argv or any(not part or "\0" in part for part in argv):
        msg = "child_argv_invalid"
        raise RuntimeAdapterError(msg)
    if any(secret and secret in part for part in argv for secret in secrets):
        msg = "secret_in_argv"
        raise RuntimeAdapterError(msg)


def _runtime_argv(
    argv: tuple[str, ...],
    *,
    platform: ExecutionPlatform,
) -> tuple[str, ...]:
    """Resolve only the allowlisted Windows command shim at process start."""
    if platform not in {"nt", "posix"}:
        msg = "unsupported_execution_platform"
        raise RuntimeAdapterError(msg)
    if platform == "nt" and argv[0] == "npx":
        return ("npx.cmd", *argv[1:])
    return argv


class DispatchRuntimeRunner:
    """Exact argv GitHub runner with bounded time and captured output."""

    def __init__(
        self,
        repository_root: Path,
        *,
        token_env: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        environ: Mapping[str, str] | None = None,
        run_process: RunProcess = subprocess.run,
    ) -> None:
        if timeout_seconds <= 0:
            msg = "child_timeout_invalid"
            raise RuntimeAdapterError(msg)
        source = dict(os.environ if environ is None else environ)
        credentials = (
            _required_environment(
                (token_env,), source, "github_token_environment_empty"
            )
            if token_env is not None
            else {}
        )
        self._environment = source
        if token_env is not None:
            self._environment["GH_TOKEN"] = credentials[token_env]
        self._secrets = tuple(credentials.values())
        self._root = repository_root.resolve(strict=True)
        self._timeout = timeout_seconds
        self._run_process = run_process

    def run(
        self,
        argv: tuple[str, ...],
        stdin: bytes | None = None,
    ) -> DispatchResult:
        _validate_argv(argv, self._secrets)
        try:
            completed = self._run_process(
                argv,
                cwd=self._root,
                env=self._environment,
                input=stdin,
                capture_output=True,
                check=False,
                shell=False,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as error:
            msg = "child_timeout"
            raise RuntimeAdapterError(msg) from error
        except OSError as error:
            msg = "child_start_failed"
            raise RuntimeAdapterError(msg) from error
        return DispatchResult(
            completed.returncode,
            _safe_output(completed.stdout, self._secrets),
            _safe_output(completed.stderr, self._secrets),
        )


class SecretRuntimeRunner:
    """No-output facade for protected-stdin commands."""

    def __init__(self, child: DispatchRuntimeRunner) -> None:
        self._child = child

    def run(self, argv: tuple[str, ...], stdin: bytes) -> int:
        result = self._child.run(argv, stdin)
        return result.returncode


class VercelRuntimeRunner:
    """Resolve declared env names before one exact Vercel child starts."""

    def __init__(
        self,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        environ: Mapping[str, str] | None = None,
        run_process: RunProcess = subprocess.run,
        platform: ExecutionPlatform = os.name,
    ) -> None:
        if timeout_seconds <= 0:
            msg = "child_timeout_invalid"
            raise RuntimeAdapterError(msg)
        self._source = dict(os.environ if environ is None else environ)
        self._timeout = timeout_seconds
        self._run_process = run_process
        self._platform = platform

    def execute(self, command: ChildCommand) -> VercelResult:
        sources = tuple(command.env.values())
        credentials = _required_environment(
            sources, self._source, "vercel_credential_environment_empty"
        )
        secrets = tuple(credentials.values())
        _validate_argv(command.argv, secrets)
        environment = self._source.copy()
        for target, source in command.env.items():
            if not target.endswith("_FROM_ENV"):
                msg = "vercel_environment_mapping_invalid"
                raise RuntimeAdapterError(msg)
            environment[target.removesuffix("_FROM_ENV")] = credentials[source]
        try:
            completed = self._run_process(
                _runtime_argv(command.argv, platform=self._platform),
                cwd=command.cwd.resolve(strict=True),
                env=environment,
                capture_output=True,
                check=False,
                shell=False,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired as error:
            msg = "child_timeout"
            raise RuntimeAdapterError(msg) from error
        except OSError as error:
            msg = "child_start_failed"
            raise RuntimeAdapterError(msg) from error
        return VercelResult(
            completed.returncode,
            _safe_output(completed.stdout, secrets),
            _safe_output(completed.stderr, secrets),
        )


__all__ = (
    "DispatchRuntimeRunner",
    "RuntimeAdapterError",
    "SecretRuntimeRunner",
    "VercelRuntimeRunner",
)
