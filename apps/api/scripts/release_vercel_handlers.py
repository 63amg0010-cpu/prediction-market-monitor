"""Public command-specific integration hooks for the release CLI."""

# ruff: noqa: D103, EM101

from __future__ import annotations

from scripts.release_vercel_alias import run_compat_alias
from scripts.release_vercel_commands import run_vercel_operation
from scripts.release_vercel_models import (
    ChildRunner,
    ReleaseHoldError,
    VercelOperation,
    VercelPrestateRequest,
)
from scripts.release_vercel_prestate import run_vercel_prestate


def run_vercel_deploy(
    request: VercelOperation,
    runner: ChildRunner,
) -> dict[str, object]:
    if request.operation == "compat-alias":
        return run_compat_alias(request, runner)
    if request.operation != "initial-deploy":
        raise ReleaseHoldError("vercel_deploy_wrong_operation")
    return run_vercel_operation(request, runner)


def run_vercel_restore(
    request: VercelOperation,
    runner: ChildRunner,
) -> dict[str, object]:
    if request.operation not in {"split-compensation", "matrix-b-rebuild"}:
        raise ReleaseHoldError("vercel_restore_wrong_operation")
    return run_vercel_operation(request, runner)


__all__ = (
    "VercelOperation",
    "VercelPrestateRequest",
    "run_vercel_deploy",
    "run_vercel_prestate",
    "run_vercel_restore",
)
