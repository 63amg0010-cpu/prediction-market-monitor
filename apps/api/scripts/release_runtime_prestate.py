"""Composite API/Web Vercel prestate rooted in protected review evidence."""

# ruff: noqa: PLR0913
# pyright: reportAny=false, reportArgumentType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, cast

import yaml
from sqlalchemy import text

from scripts.release_dispatch_contracts import canonical_bytes
from scripts.release_evidence_review import validate_review_record
from scripts.release_runtime_io import GitStatReviewAdapter
from scripts.release_runtime_subprocess import VercelRuntimeRunner
from scripts.release_vercel_models import (
    CLI_VERSION,
    VercelPrestateRequest,
)
from scripts.release_vercel_prestate import run_vercel_prestate

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncEngine


class PrestateRuntimeError(RuntimeError):
    """Stable composite prestate error."""


def _review_front_matter(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---\n") or "\n---\n" not in raw[4:]:
        msg = "review_record_front_matter_invalid"
        raise PrestateRuntimeError(msg)
    parsed = cast("object", yaml.safe_load(raw[4:].split("\n---\n", 1)[0]))
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) for key in parsed
    ):
        msg = "review_record_front_matter_invalid"
        raise PrestateRuntimeError(msg)
    return cast("dict[str, object]", parsed)


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


async def _database_time(engine: AsyncEngine) -> datetime:
    try:
        async with engine.connect() as connection, connection.begin():
            _ = await connection.execute(
                text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
            )
            value = await connection.scalar(
                text("SELECT transaction_timestamp()")
            )
            if not isinstance(value, datetime):
                msg = "database_time_invalid"
                raise PrestateRuntimeError(msg)
            return value
    finally:
        await engine.dispose()


def _database_time_from_isolated_thread(engine: AsyncEngine) -> datetime:
    """Keep a synchronous caller's current event loop untouched on Windows."""

    def execute() -> datetime:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            return runner.run(_database_time(engine))

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(execute).result()


def capture_composite_prestate(
    *,
    repository_root: Path,
    engine: AsyncEngine,
    review_record: Path,
    live_plan: Path,
    expected_sha: str,
    activation_nonce: UUID,
    team_slug: str,
    org_id_env: str,
    api_project_name: str,
    api_project_id_env: str,
    web_project_name: str,
    web_project_id_env: str,
    token_env: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Capture both projects and emit one root receipt with predecessor null."""
    source = os.environ if environ is None else environ
    names = (
        org_id_env,
        api_project_id_env,
        web_project_id_env,
        token_env,
    )
    if any(not name or not source.get(name) for name in names):
        msg = "vercel_credential_environment_empty"
        raise PrestateRuntimeError(msg)
    root = repository_root.resolve(strict=True)
    plan = live_plan.resolve(strict=True)
    _ = plan.relative_to(root)
    document = _review_front_matter(review_record)
    plan_name = document.get("plan_path")
    if not isinstance(plan_name, str):
        msg = "review_record_plan_path_invalid"
        raise PrestateRuntimeError(msg)
    if (root / plan_name).resolve(strict=True) != plan:
        msg = "live_plan_path_mismatch"
        raise PrestateRuntimeError(msg)
    bindings = validate_review_record(
        document,
        access=GitStatReviewAdapter(root).inspect(review_record),
        live_plan_path=plan_name,
        live_plan_bytes=plan.read_bytes(),
        expected_sha=expected_sha,
    )
    runner = VercelRuntimeRunner(environ=source)
    projects: list[dict[str, object]] = []
    for kind, name, project_env in (
        ("api", api_project_name, api_project_id_env),
        ("web", web_project_name, web_project_id_env),
    ):
        receipt = run_vercel_prestate(
            VercelPrestateRequest(
                repository_root=root,
                project_kind=cast("object", kind),  # type: ignore[arg-type]
                team_slug=team_slug,
                org_id_env=org_id_env,
                project_name=name,
                project_id_env=project_env,
                token_env=token_env,
                protected_ref="origin/main",
                expected_sha=expected_sha,
                expected_plan_sha256=bindings.approved_plan_sha256,
                activation_nonce=activation_nonce,
                cli_version=CLI_VERSION,
            ),
            runner,
        )
        projects.append(
            {
                "kind": kind,
                "project_name": name,
                "project_identity_sha256": _hash(source[project_env]),
                "deployment_identity_sha256": _hash(
                    str(receipt["deployment_id"])
                ),
                "deployment_url_sha256": _hash(
                    str(receipt["deployment_url"])
                ),
                "alias": receipt["alias"],
                "protected_source_sha": receipt["protected_source_sha"],
                "ready_state": "READY",
                "environment": "production",
            }
        )
    observed_at = _database_time_from_isolated_thread(engine)
    body: dict[str, object] = {
        "schema_version": 1,
        "command": "deployment-prestate",
        "reviewed_sha": bindings.reviewed_sha,
        "approved_plan_sha256": bindings.approved_plan_sha256,
        "approval_round_id": bindings.approval_round_id,
        "approval_launch_sha256s": list(bindings.approval_launch_sha256s),
        "activation_nonce": str(activation_nonce),
        "database_time": observed_at.isoformat(),
        "team_identity_sha256": _hash(source[org_id_env]),
        "projects": projects,
        "accepted": True,
        "predecessor_receipt_sha256": None,
    }
    return {**body, "receipt_sha256": sha256(canonical_bytes(body)).hexdigest()}


__all__ = ("PrestateRuntimeError", "capture_composite_prestate")
