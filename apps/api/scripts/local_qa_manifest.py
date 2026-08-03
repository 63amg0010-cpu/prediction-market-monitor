"""Canonical Todo 11 command manifest shared by both operating-system wrappers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, TypeAlias

if TYPE_CHECKING:
    from pathlib import Path

Argv: TypeAlias = tuple[str, ...]  # noqa: UP040 - wrappers support system Python 3.11.

EXPECTED_DATABASE: Final = "monitor_migration_qa"
REQUIRED_START: Final = "20260726_0009"
EXPECTED_HEAD: Final = "20260803_0012"
GUARD_FILE: Final = "apps/api/tests/fixtures/release-gate/local-qa-db-guard.json"
GATE_PREFIX: Final = (
    "uv",
    "run",
    "--package",
    "monitor-api",
    "python",
    "apps/api/scripts/fresh_search_release_gate.py",
)
FAILURE_POINTS: Final = (
    "provision",
    *(f"command-{number:02d}" for number in range(1, 21)),
    "dispose",
)
OPENAPI_COMMAND: Final = (
    "uv", "run", "--package", "monitor-api", "python", "-m", "app.openapi"
)


def provision_argv(
    attempt_dir: Path,
    admin_env: str,
    database_env: str,
) -> Argv:
    """Return the mandatory pre-command reprovision invocation."""
    return (
        *GATE_PREFIX,
        "local-db",
        "--phase",
        "reprovision",
        "--admin-database-url-env",
        admin_env,
        "--database-url-env",
        database_env,
        "--expected-database",
        EXPECTED_DATABASE,
        "--required-start",
        REQUIRED_START,
        "--guard-file",
        GUARD_FILE,
        "--json-out",
        str(attempt_dir / "migration-provision.json"),
    )


def dispose_argv(
    attempt_dir: Path,
    admin_env: str,
    database_env: str,
) -> Argv:
    """Return the mandatory finally-owned disposal invocation."""
    return (
        *GATE_PREFIX,
        "local-db",
        "--phase",
        "dispose",
        "--admin-database-url-env",
        admin_env,
        "--database-url-env",
        database_env,
        "--expected-database",
        EXPECTED_DATABASE,
        "--guard-file",
        GUARD_FILE,
        "--json-out",
        str(attempt_dir / "migration-dispose.json"),
    )


def runtime_argv(argv: Argv, *, platform: str) -> Argv:
    """Resolve only the allowlisted Windows pnpm command shim."""
    if platform not in {"nt", "posix"}:
        error_code = "unsupported_execution_platform"
        raise RuntimeError(error_code)
    if platform == "nt" and argv[0] == "pnpm":
        return ("pnpm.cmd", *argv[1:])
    return argv


def ordered_commands(
    attempt_dir: Path,
    base_sha: str,
    reviewed_sha: str,
    database_env: str,
) -> tuple[Argv, ...]:
    """Materialize the exact normative 20-command argv sequence."""
    gate = GATE_PREFIX
    return (
        ("uv", "sync", "--frozen", "--all-packages"),
        ("pnpm", "install", "--frozen-lockfile"),
        (
            "uv",
            "run",
            "--all-packages",
            "ruff",
            "check",
            "apps/api/app",
            "apps/api/scripts",
            "apps/api/tests",
            "workers/codex-worker/src",
            "workers/codex-worker/tests",
        ),
        (
            "uv",
            "run",
            "--all-packages",
            "basedpyright",
            "apps/api/app",
            "apps/api/scripts",
            "apps/api/tests",
            "workers/codex-worker/src",
            "workers/codex-worker/tests",
        ),
        (
            "uv",
            "run",
            "--package",
            "monitor-api",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(attempt_dir / "pytest-command-05"),
            "apps/api/tests/contracts",
            "apps/api/tests/unit",
            "-q",
        ),
        (
            "uv",
            "run",
            "--package",
            "monitor-api",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(attempt_dir / "pytest-command-06"),
            "apps/api/tests/integration/test_postgres_report_retention.py",
            "apps/api/tests/integration/test_collector_workflow.py",
            "apps/api/tests/integration/test_dashboard_api.py",
            "apps/api/tests/integration/test_verification.py",
            "-q",
            "-rs",
        ),
        (
            "uv",
            "run",
            "--package",
            "monitor-api",
            "pytest",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(attempt_dir / "pytest-command-07"),
            "apps/api/tests/migrations/test_20260727_manifold_search.py",
            "apps/api/tests/migrations/test_20260727_prepare_manifold.py",
            "-q",
        ),
        (
            "uv",
            "run",
            "--package",
            "monitor-api",
            "alembic",
            "-c",
            "apps/api/alembic.ini",
            "heads",
        ),
        (
            *gate,
            "local-db",
            "--phase",
            "upgrade",
            "--database-url-env",
            database_env,
            "--expected-database",
            EXPECTED_DATABASE,
            "--required-start",
            REQUIRED_START,
            "--target",
            EXPECTED_HEAD,
            "--json-out",
            str(attempt_dir / "migration-upgrade.json"),
        ),
        (
            *gate,
            "local-db",
            "--phase",
            "verify",
            "--database-url-env",
            database_env,
            "--expected-database",
            EXPECTED_DATABASE,
            "--expected-head",
            EXPECTED_HEAD,
            "--expected-current",
            EXPECTED_HEAD,
            "--expected-index",
            "ix_post_versions_search_text_trgm",
            "--json-out",
            str(attempt_dir / "migration-local.json"),
        ),
        OPENAPI_COMMAND,
        ("pnpm", "--filter", "@prediction-market/web", "check:api"),
        ("pnpm", "--filter", "@prediction-market/web", "test"),
        ("pnpm", "--filter", "@prediction-market/web", "typecheck"),
        ("pnpm", "--filter", "@prediction-market/web", "lint"),
        ("pnpm", "--filter", "@prediction-market/web", "build"),
        (
            *gate,
            "secret-static-scan",
            "--root",
            ".",
            "--base-sha",
            base_sha,
            "--reviewed-sha",
            reviewed_sha,
            "--json-out",
            str(attempt_dir / "secret-static-scan.json"),
        ),
        (
            *gate,
            "plan-compliance",
            "--root",
            ".",
            "--json-out",
            str(attempt_dir / "plan-compliance.json"),
        ),
        (
            *gate,
            "scope-fidelity",
            "--root",
            ".",
            "--json-out",
            str(attempt_dir / "scope-fidelity.json"),
        ),
        (
            *gate,
            "links",
            "--root",
            ".",
            "--paths",
            "README.md",
            "docs",
            "--json-out",
            str(attempt_dir / "docs-links.json"),
        ),
    )


__all__ = (
    "FAILURE_POINTS",
    "OPENAPI_COMMAND",
    "Argv",
    "dispose_argv",
    "ordered_commands",
    "provision_argv",
    "runtime_argv",
)
