"""Guarded lifecycle for the exact disposable local migration QA database."""

# ruff: noqa: EM101

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from scripts.local_db_guard import (
    LocalDatabaseHoldError,
    guarded_admin,
    guarded_target,
    load_guard,
)
from scripts.local_db_verification import (
    baseline_checks,
    current_revision,
    verify_final,
)

if TYPE_CHECKING:
    import argparse

    from sqlalchemy.engine import URL

ROOT: Final = Path(__file__).resolve().parents[3]
ALEMBIC_ARGV: Final = (
    "uv",
    "run",
    "--package",
    "monitor-api",
    "alembic",
    "-c",
    "apps/api/alembic.ini",
    "upgrade",
)


class SubparserRegistry(Protocol):
    """Minimum argparse registry surface required by the integration owner."""

    def add_parser(self, name: str) -> argparse.ArgumentParser:
        """Add one named parser."""
        ...


class LocalDbNamespace(Protocol):
    """Typed attributes supplied by the shared argparse namespace."""

    phase: str
    database_url_env: str
    expected_database: str
    json_out: str
    admin_database_url_env: str | None
    required_start: str | None
    target: str | None
    guard_file: str | None
    expected_head: str | None
    expected_current: str | None
    expected_index: str | None


@dataclass(frozen=True, slots=True)
class LocalDbRequest:
    """Typed, already parsed local-db request."""

    phase: str
    database_url_env: str
    expected_database: str
    json_out: Path
    admin_database_url_env: str | None = None
    required_start: str | None = None
    target: str | None = None
    guard_file: Path | None = None
    expected_head: str | None = None
    expected_current: str | None = None
    expected_index: str | None = None


def register_local_db_parser(registry: SubparserRegistry) -> argparse.ArgumentParser:
    """Register the subcommand without modifying the shared parser module."""
    parser = registry.add_parser("local-db")
    _ = parser.add_argument(
        "--phase",
        required=True,
        choices=("reprovision", "upgrade", "verify", "dispose"),
    )
    for name in ("database-url-env", "expected-database", "json-out"):
        _ = parser.add_argument(f"--{name}", required=True)
    for name in (
        "admin-database-url-env",
        "required-start",
        "target",
        "guard-file",
        "expected-head",
        "expected-current",
        "expected-index",
    ):
        _ = parser.add_argument(f"--{name}")
    return parser


def request_from_namespace(args: LocalDbNamespace) -> LocalDbRequest:
    """Copy argparse values into a closed typed request."""
    return LocalDbRequest(
        phase=args.phase,
        database_url_env=args.database_url_env,
        expected_database=args.expected_database,
        json_out=Path(args.json_out),
        admin_database_url_env=args.admin_database_url_env,
        required_start=args.required_start,
        target=args.target,
        guard_file=_optional_path(args.guard_file),
        expected_head=args.expected_head,
        expected_current=args.expected_current,
        expected_index=args.expected_index,
    )


async def execute_local_db(request: LocalDbRequest) -> int:
    """Execute one phase and write only a canonical redacted receipt."""
    target_secret = os.environ.get(request.database_url_env, "")
    target = guarded_target(target_secret, request.expected_database)
    if request.phase in {"reprovision", "dispose"}:
        checks = await _maintenance_phase(request, target, target_secret)
    elif request.phase == "upgrade":
        checks = await _upgrade(request, target_secret)
    elif request.phase == "verify":
        checks = await _verify(request, target_secret)
    else:
        raise LocalDatabaseHoldError("local_db_phase_invalid")
    _write_receipt(request, checks)
    return 0


def run_local_db(request: LocalDbRequest) -> int:
    """Synchronous integration hook for a shared command dispatcher."""
    return asyncio.run(execute_local_db(request))


async def _maintenance_phase(
    request: LocalDbRequest,
    target: URL,
    target_secret: str,
) -> dict[str, object]:
    if request.admin_database_url_env is None or request.guard_file is None:
        raise LocalDatabaseHoldError("maintenance_arguments_required")
    guard = load_guard(request.guard_file)
    admin_secret = os.environ.get(request.admin_database_url_env, "")
    admin = guarded_admin(admin_secret, target, guard)
    engine = create_async_engine(admin, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            _ = await connection.execute(
                text("SELECT pg_advisory_lock(hashtext(:key))"),
                {"key": guard.advisory_lock},
            )
            try:
                if request.phase == "dispose":
                    await _drop_database(connection, request.expected_database)
                    return {"database_absent": True, "advisory_lock": True}
                if request.required_start is None:
                    raise LocalDatabaseHoldError("required_start_missing")
                await _drop_database(connection, request.expected_database)
                _ = await connection.execute(
                    text(f'CREATE DATABASE "{request.expected_database}"')
                )
                _alembic_upgrade(target_secret, request.required_start)
                baseline = await baseline_checks(target_secret, request.required_start)
                return {"database_recreated": True, "advisory_lock": True, **baseline}
            finally:
                _ = await connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:key))"),
                    {"key": guard.advisory_lock},
                )
    finally:
        await engine.dispose()


async def _drop_database(connection: AsyncConnection, database: str) -> None:
    _ = await connection.execute(
        text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
    )


async def _upgrade(request: LocalDbRequest, target_secret: str) -> dict[str, object]:
    if request.required_start is None or request.target is None:
        raise LocalDatabaseHoldError("upgrade_arguments_required")
    baseline = await baseline_checks(target_secret, request.required_start)
    _alembic_upgrade(target_secret, request.target)
    current = await current_revision(target_secret)
    if current != request.target:
        raise LocalDatabaseHoldError("target_revision_not_reached")
    return {**baseline, "target_revision": current}


async def _verify(request: LocalDbRequest, url: str) -> dict[str, object]:
    if (
        not request.expected_head
        or not request.expected_current
        or not request.expected_index
    ):
        raise LocalDatabaseHoldError("verify_arguments_required")
    return await verify_final(
        url=url,
        expected_head=request.expected_head,
        expected_current=request.expected_current,
        expected_index=request.expected_index,
    )


def _alembic_upgrade(url: str, revision: str) -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = url
    environment["MIGRATION_DATABASE_URL"] = url
    completed = subprocess.run(  # noqa: S603
        (*ALEMBIC_ARGV, revision),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise LocalDatabaseHoldError("alembic_upgrade_failed")


def _write_receipt(request: LocalDbRequest, checks: dict[str, object]) -> None:
    body = {
        "schema": "release-gate.local-db-result.v1",
        "phase": request.phase,
        "accepted": True,
        "expected_database": request.expected_database,
        "checks": checks,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    output = {**body, "receipt_sha256": hashlib.sha256(canonical).hexdigest()}
    request.json_out.parent.mkdir(parents=True, exist_ok=True)
    _ = request.json_out.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _optional_path(value: object) -> Path | None:
    return None if value is None else Path(str(value))


__all__ = (
    "LocalDbRequest",
    "execute_local_db",
    "register_local_db_parser",
    "request_from_namespace",
    "run_local_db",
)
