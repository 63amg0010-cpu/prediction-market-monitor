from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml
from app.domain.types import JsonValue
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[4]
YAML_ADAPTER = TypeAdapter(dict[str, JsonValue])
STRING_LIST_ADAPTER = TypeAdapter(list[str])


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _yaml(relative: str) -> dict[str, JsonValue]:
    return YAML_ADAPTER.validate_python(yaml.safe_load(_read(relative)))


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _strings(value: JsonValue) -> tuple[str, ...]:
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in value)
    return tuple(item for item in value if isinstance(item, str))


def _workflow_environments(source: str) -> set[str]:
    return set(re.findall(r"(?m)^\s+environment:\s+([a-z][a-z0-9-]*)\s*$", source))


def test_api_dockerfile_is_multistage_frozen_and_non_root() -> None:
    # Given: the production API container definition.
    dockerfile = _read("apps/api/Dockerfile")

    # When/Then: it builds a frozen runtime and starts the real ASGI app.
    assert "FROM python:3.12.8-slim-bookworm AS builder" in dockerfile
    assert "FROM python:3.12.8-slim-bookworm AS runtime" in dockerfile
    assert "ghcr.io/astral-sh/uv:0.5.30" in dockerfile
    assert "uv sync --frozen --package monitor-api --no-dev" in dockerfile
    assert "COPY apps/api/app" in dockerfile
    assert "COPY apps/api/migrations" in dockerfile
    assert "COPY config" in dockerfile
    assert "USER monitor" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert 'CMD ["uvicorn", "app.main:app"' in dockerfile


def test_web_dockerfile_builds_standalone_runtime_without_dev_command() -> None:
    # Given: the production web container definition and Next config.
    dockerfile = _read("apps/web/Dockerfile")
    next_config = _read("apps/web/next.config.js")

    # When/Then: it builds standalone output and runs with Node only.
    assert "FROM node:22.14.0-alpine AS builder" in dockerfile
    assert "FROM node:22.14.0-alpine AS runtime" in dockerfile
    assert "corepack prepare pnpm@9.15.4 --activate" in dockerfile
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "pnpm --filter @prediction-market/web build" in dockerfile
    assert ".next/standalone" in dockerfile
    assert "USER node" in dockerfile
    assert 'CMD ["node", "server.js"]' in dockerfile
    assert "pnpm dev" not in dockerfile
    assert 'output: "standalone"' in next_config


def test_compose_uses_container_urls_and_health_dependencies() -> None:
    # Given: Docker Compose wires service-network URLs into containers.
    compose = _yaml("docker-compose.yml")
    services = _mapping(compose["services"])
    api = _mapping(services["api"])
    web = _mapping(services["web"])
    api_env = _mapping(api["environment"])
    web_env = _mapping(web["environment"])

    # When/Then: host URLs are not passed into container-only dependencies.
    assert api_env["DATABASE_URL"] == (
        "${CONTAINER_DATABASE_URL:?CONTAINER_DATABASE_URL must be set}"
    )
    assert web_env["API_BASE_URL"] == (
        "${CONTAINER_API_BASE_URL:?CONTAINER_API_BASE_URL must be set}"
    )
    assert "healthcheck" in api
    assert "healthcheck" in web
    assert "service_healthy" in yaml.safe_dump(web["depends_on"])


def test_env_docs_and_windows_script_keep_host_and_container_urls_separate() -> None:
    # Given: beginner-facing setup must be executable from Windows host.
    env_example = _read(".env.example")
    windows_doc = _read("docs/windows-setup.md")
    readme = _read("README.md")
    script = _read("scripts/windows/Verify-LocalSetup.ps1")

    # When/Then: host, migration and container URLs are distinct and checked.
    host_url = (
        "DATABASE_URL=postgresql+asyncpg://monitor:"
        "<url-encoded-password>@localhost:5432"
    )
    migration_url = (
        "MIGRATION_DATABASE_URL=postgresql+asyncpg://monitor:"
        "<url-encoded-password>@localhost:5432"
    )
    dump_url = (
        "PG_DUMP_DATABASE_URL=postgresql://monitor:"
        "<url-encoded-password>@localhost:5432"
    )
    restore_url = (
        "PG_RESTORE_DATABASE_URL=postgresql://monitor:"
        "<url-encoded-password>@localhost:5432"
    )
    container_url = (
        "CONTAINER_DATABASE_URL=postgresql+asyncpg://monitor:"
        "<url-encoded-password>@db:5432"
    )
    assert host_url in env_example
    assert migration_url in env_example
    assert dump_url in env_example
    assert restore_url in env_example
    assert container_url in env_example
    assert "CONTAINER_API_BASE_URL=http://api:8000" in env_example
    assert "HOST_DATABASE_URL" in windows_doc
    assert "CONTAINER_DATABASE_URL" in windows_doc
    assert "Get-Content .env" in windows_doc
    assert "HOST_DATABASE_URL" in readme
    assert '$RequiredUvVersion = "0.5.30"' in script
    assert '$RequiredNodeVersion = "22.14.0"' in script
    assert '"MIGRATION_DATABASE_URL"' in script
    assert '"PG_DUMP_DATABASE_URL"' in script
    assert '"PG_RESTORE_DATABASE_URL"' in script
    assert '"CONTAINER_DATABASE_URL"' in script
    operator_sources = (
        _read("docs/cloud-deployment-handoff.md"),
        _read("docs/runbook.md"),
        _read(".github/workflows/migrate.yml"),
    )
    for source in operator_sources:
        assert not re.search(
            r"pg_(?:dump|restore)[^\n]*MIGRATION_DATABASE_URL",
            source,
        )


def test_windows_exact_version_parser_rejects_numeric_near_misses() -> None:
    # Given: the production setup checker and its executable version probe.
    setup_script = _read("scripts/windows/Verify-LocalSetup.ps1")
    parser = _read("scripts/windows/ExactToolVersion.ps1")
    probe = _read("scripts/windows/Test-ExactToolVersion.ps1")

    # When/Then: the shared parser accepts exact vendor forms and rejects
    # longer or zero-padded numeric tokens.
    assert ".Contains($RequiredVersion)" not in setup_script
    assert '"uv 0.5.30"' in probe
    assert '"uv 0.5.30 (c4d0caa14 2025-02-12 x86_64-pc-windows-msvc)"' in probe
    assert '"v22.14.0"' in probe
    assert '"uv 0.5.300"' in probe
    assert '"v22.14.01"' in probe
    assert "[regex]::IsMatch" in parser
    assert "StringComparison]::Ordinal" in parser


def test_cloud_handoff_contract_matches_workflows_and_environment_template() -> None:
    # Given: the machine-readable companion to the beginner cloud guide.
    contract = _yaml("docs/cloud-deployment-handoff.yml")
    github = _mapping(contract["github"])
    vercel = _mapping(contract["vercel"])
    supabase = _mapping(contract["supabase"])
    acceptance = _mapping(contract["acceptance"])

    # When/Then: workflow environments, roots, credentials and evidence counts
    # agree with the executable repository configuration.
    workflow_sources = tuple(
        _read(f".github/workflows/{name}.yml")
        for name in ("ci", "collect", "verify", "migrate")
    )
    workflow_environments: set[str] = set()
    for source in workflow_sources:
        workflow_environments.update(_workflow_environments(source))
    assert set(_strings(github["environments"])) == workflow_environments
    for secret_name in _strings(github["workflow_secret_names"]):
        assert any(secret_name in source for source in workflow_sources)

    assert vercel["api_root"] == "apps/api"
    assert vercel["web_root"] == "apps/web"
    assert vercel["cli_cwd"] == "repository_root"
    assert vercel["ci_project_selection"] == "matrix_vercel_project_id"
    assert vercel["build_output"] == ".vercel/output"
    assert supabase["runtime_connection"] == "transaction_pooler"
    assert supabase["migration_connection"] == "direct_async_sqlalchemy"
    assert supabase["backup_connection"] == "direct_native_libpq"
    assert acceptance == {
        "consecutive_utc_days": 30,
        "collection_slots": 240,
        "verifier_slots": 2880,
        "allowed_missing_verifier_slots": 0,
    }

    env_lines = (
        line.split("=", 1)
        for line in _read(".env.example").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    env = dict(env_lines)
    workflow_refs = tuple(
        STRING_LIST_ADAPTER.validate_json(env["GITHUB_WORKFLOW_REFS"])
    )
    allowed_refs = tuple(STRING_LIST_ADAPTER.validate_json(env["GITHUB_ALLOWED_REFS"]))
    allowed_environments = tuple(
        STRING_LIST_ADAPTER.validate_json(env["GITHUB_ALLOWED_ENVIRONMENTS"])
    )
    assert workflow_refs == _strings(github["oidc_workflow_refs"])
    assert allowed_refs == _strings(github["oidc_allowed_refs"])
    assert allowed_environments == _strings(github["oidc_allowed_environments"])
    assert len(workflow_refs) == len(allowed_refs) == len(allowed_environments)
    guide = _read(str(contract["guide"]))
    environment_keys = (
        *_strings(vercel["api_environment_keys"]),
        *_strings(vercel["web_environment_keys"]),
    )
    for key in environment_keys:
        assert key in guide


def test_cloud_handoff_is_linked_from_every_operator_entrypoint() -> None:
    # Given: every document a beginner may open first.
    entrypoints = (
        Path("docs/free-tier-operations.md"),
        Path("docs/runbook.md"),
        Path("docs/windows-setup.md"),
    )

    # When/Then: each entrypoint links to the same existing cloud procedure.
    for entrypoint in entrypoints:
        links = re.findall(r"\[[^]]+\]\(([^)]+)\)", _read(entrypoint.as_posix()))
        assert "cloud-deployment-handoff.md" in links
        assert (ROOT / entrypoint.parent / "cloud-deployment-handoff.md").is_file()


def test_phase5_done_claims_reference_current_green_hashed_evidence() -> None:
    # Given: public completion claims and their current source/evidence manifest.
    evidence_root = "docs/evidence/deployment-validation"
    summary = _read(f"{evidence_root}/summary.md")
    done_claim = summary.split("## Scope", 1)[0]
    paths = tuple(
        match.group(1)
        for match in re.finditer(
            rf"`({evidence_root}/[^`]+)`", done_claim
        )
    )
    current_sources = (
        ".env.example",
        ".github/workflows/ci.yml",
        ".github/workflows/collect.yml",
        ".github/workflows/migrate.yml",
        ".github/workflows/verify.yml",
        "apps/api/vercel.json",
        "apps/web/vercel.json",
        "docs/cloud-deployment-handoff.md",
        "docs/cloud-deployment-handoff.yml",
    )
    hashes = set(_read(f"{evidence_root}/evidence-hashes.txt").splitlines())

    # When/Then: claims cite no red artifact and all current digests are recorded.
    assert paths
    for relative in (*paths, *current_sources):
        path = ROOT / relative
        assert "red" not in path.name.lower(), relative
        assert path.exists(), relative
        assert path.stat().st_size > 0, relative
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        assert f"{digest}  {relative}" in hashes, relative
