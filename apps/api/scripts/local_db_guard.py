"""Closed local-only database target validation for the release QA gate."""

# ruff: noqa: EM101

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

if TYPE_CHECKING:
    from pathlib import Path

EXPECTED_DATABASE: Final = "monitor_migration_qa"
ALLOWED_HOSTS: Final = frozenset({"127.0.0.1", "::1", "localhost", "db"})
ALLOWED_ADMIN_DATABASES: Final = frozenset({"postgres"})
FORBIDDEN_HOST_SUFFIXES: Final = (
    ".supabase.co",
    ".supabase.com",
    ".vercel-storage.com",
)
SCHEMA: Final = "release-gate.local-qa-db-guard.v1"


class LocalDatabaseHoldError(RuntimeError):
    """Stable redacted refusal from the local database boundary."""


class _GuardDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    schema_name: Literal["release-gate.local-qa-db-guard.v1"] = Field(alias="schema")
    environment: Literal["test"]
    expected_database: str
    allowed_hosts: list[str]
    allowed_admin_databases: list[str]
    advisory_lock: str


@dataclass(frozen=True, slots=True)
class Guard:
    """Committed allowlist for one disposable local database."""

    expected_database: str
    allowed_hosts: frozenset[str]
    allowed_admin_databases: frozenset[str]
    advisory_lock: str


def load_guard(path: Path) -> Guard:
    """Load a schema-closed committed guard without accepting aliases."""
    try:
        raw = _GuardDocument.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as error:
        raise LocalDatabaseHoldError("guard_file_invalid") from error
    if (
        raw.expected_database != EXPECTED_DATABASE
        or frozenset(raw.allowed_hosts) != ALLOWED_HOSTS
        or frozenset(raw.allowed_admin_databases) != ALLOWED_ADMIN_DATABASES
        or re.fullmatch(r"[a-z0-9_-]{8,80}", raw.advisory_lock) is None
    ):
        raise LocalDatabaseHoldError("guard_values_invalid")
    return Guard(
        expected_database=EXPECTED_DATABASE,
        allowed_hosts=ALLOWED_HOSTS,
        allowed_admin_databases=ALLOWED_ADMIN_DATABASES,
        advisory_lock=raw.advisory_lock,
    )


def guarded_target(raw_url: str, expected_database: str) -> URL:
    """Accept only the exact disposable DB on loopback or the test container."""
    if expected_database != EXPECTED_DATABASE:
        raise LocalDatabaseHoldError("expected_database_not_exact")
    parsed = _parse(raw_url)
    host = (parsed.host or "").lower().rstrip(".")
    if any(host.endswith(suffix) for suffix in FORBIDDEN_HOST_SUFFIXES):
        raise LocalDatabaseHoldError("production_or_supabase_host_refused")
    if host not in ALLOWED_HOSTS:
        raise LocalDatabaseHoldError("database_host_not_local")
    if parsed.database != expected_database:
        raise LocalDatabaseHoldError("database_name_not_exact")
    return parsed


def guarded_admin(raw_url: str, target: URL, guard: Guard) -> URL:
    """Accept a maintenance DB colocated with the already-guarded target."""
    parsed = _parse(raw_url)
    host = (parsed.host or "").lower().rstrip(".")
    if (
        host not in guard.allowed_hosts
        or parsed.database not in guard.allowed_admin_databases
        or host != (target.host or "").lower().rstrip(".")
        or parsed.port != target.port
    ):
        raise LocalDatabaseHoldError("admin_database_not_local_maintenance")
    return parsed


def _parse(raw_url: str) -> URL:
    if not raw_url:
        raise LocalDatabaseHoldError("database_environment_empty")
    try:
        parsed = make_url(raw_url)
    except ArgumentError as error:
        raise LocalDatabaseHoldError("database_url_invalid") from error
    if (
        parsed.drivername != "postgresql+asyncpg"
        or parsed.host is None
        or parsed.database is None
    ):
        raise LocalDatabaseHoldError("database_url_not_async_postgresql")
    return parsed


__all__ = (
    "EXPECTED_DATABASE",
    "Guard",
    "LocalDatabaseHoldError",
    "guarded_admin",
    "guarded_target",
    "load_guard",
)
