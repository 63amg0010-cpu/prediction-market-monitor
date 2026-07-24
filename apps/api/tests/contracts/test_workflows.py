from __future__ import annotations

from pathlib import Path

import yaml
from app.domain.types import JsonValue
from pydantic import TypeAdapter

ROOT = Path(__file__).resolve().parents[4]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW_ADAPTER = TypeAdapter(dict[str, JsonValue])
PINNED_ACTIONS = {
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020",
    "astral-sh/setup-uv@d0d8abe699bfb85fec6de9f7adb5ae17292296ff",
}


def _workflow(name: str) -> dict[str, JsonValue]:
    source = (WORKFLOWS / name).read_text(encoding="utf-8")
    normalized = source.replace("\non:\n", "\n'on':\n", 1)
    return WORKFLOW_ADAPTER.validate_python(yaml.safe_load(normalized))


def _mapping(value: JsonValue) -> dict[str, JsonValue]:
    assert isinstance(value, dict)
    return value


def _job(workflow: dict[str, JsonValue], name: str) -> dict[str, JsonValue]:
    return _mapping(_mapping(workflow["jobs"])[name])


def _steps(job: dict[str, JsonValue]) -> list[JsonValue]:
    steps = job["steps"]
    assert isinstance(steps, list)
    return steps


def _int(value: JsonValue) -> int:
    assert isinstance(value, int)
    return value


def test_collect_workflow_uses_minute_17_oidc_bounded_cli() -> None:
    # Given: the sole source-network collection workflow.
    workflow = _workflow("collect.yml")
    triggers = _mapping(workflow["on"])
    permissions = _mapping(workflow["permissions"])
    job = _job(workflow, "collect")

    # When/Then: it runs every three hours with OIDC and a six-minute bound.
    assert triggers["schedule"] == [{"cron": "17 */3 * * *"}]
    assert permissions["id-token"] == "write"
    assert job["timeout-minutes"] == 6
    rendered = yaml.safe_dump(workflow)
    assert "app.collection.cli collect" in rendered
    assert "MONITOR_DEPLOYMENT_ACTIVATION_AT" in rendered
    assert "MONITOR_SOURCE_BINDINGS_JSON" in rendered
    assert "DATABASE_URL" not in rendered
    assert "SUPABASE" not in rendered.upper()
    assert "BEARER_TOKEN" not in rendered


def test_collect_workflow_never_executes_independent_verifier() -> None:
    # Given: the collector workflow with collector-only environment binding.
    workflow = _workflow("collect.yml")
    job = _job(workflow, "collect")

    # When/Then: collection cannot cross the independent verifier boundary.
    rendered = yaml.safe_dump(workflow)
    assert job["environment"] == "production-collector"
    assert "app.collection.cli verify" not in rendered
    assert "production-verifier" not in rendered
    assert "VERIFY_READ" not in rendered
    assert "VERIFY_WRITE" not in rendered


def test_verifier_has_exact_independent_public_schedule_and_manual_private_gate() -> (
    None
):
    # Given: collection and independent freshness verification workflows.
    collect_workflow = _workflow("collect.yml")
    workflow = _workflow("verify.yml")
    triggers = _mapping(workflow["on"])
    permissions = _mapping(workflow["permissions"])
    job = _job(workflow, "verify")

    # When/Then: verification has its own exact schedule and cannot be
    # substituted by the collector's three-hour scoped verification.
    collect_schedule = _mapping(collect_workflow["on"])["schedule"]
    assert isinstance(collect_schedule, list)
    assert _mapping(collect_schedule[0]) == {"cron": "17 */3 * * *"}
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert triggers["schedule"] == [{"cron": "*/15 * * * *"}]
    assert set(_mapping(workflow["jobs"])) == {"verify"}
    assert "needs" not in job
    dispatch = _mapping(triggers["workflow_dispatch"])
    inputs = _mapping(dispatch["inputs"])
    authorization = _mapping(inputs["authorize_private_minutes"])
    assert authorization["type"] == "boolean"
    assert authorization["default"] is False
    assert job["if"] == (
        "${{ github.event.repository.private == false || "
        "(github.event_name == 'workflow_dispatch' && "
        "inputs.authorize_private_minutes == true) }}"
    )
    assert permissions["id-token"] == "write"
    assert job["timeout-minutes"] == 3
    rendered = yaml.safe_dump(workflow).upper()
    assert "APP.COLLECTION.CLI VERIFY" in rendered
    assert "REDDIT" not in rendered
    assert "DATABASE_URL" not in rendered
    assert "SUPABASE" not in rendered
    assert "BEARER_TOKEN" not in rendered


def test_migration_workflow_is_manual_confirmed_and_environment_protected() -> None:
    # Given: the only workflow allowed a migration database connection.
    workflow = _workflow("migrate.yml")
    triggers = _mapping(workflow["on"])
    job = _job(workflow, "migrate")

    # When/Then: no schedule exists and explicit protected approval is required.
    assert set(triggers) == {"workflow_dispatch"}
    assert job["environment"] == "production-migration"
    assert "confirm" in str(job["if"])
    assert job["timeout-minutes"] == 10
    steps = _steps(job)
    named_steps = {
        str(step["name"]): step
        for value in steps
        if isinstance(value, dict)
        for step in (_mapping(value),)
        if "name" in step
    }
    dump_step = named_steps["Export pre-migration backup"]
    restore_step = named_steps["Roll back failed migration from ephemeral backup"]
    assert _mapping(dump_step["env"])["PG_DUMP_DATABASE_URL"] == (
        "${{ secrets.PG_DUMP_DATABASE_URL }}"
    )
    assert _mapping(restore_step["env"])["PG_RESTORE_DATABASE_URL"] == (
        "${{ secrets.PG_RESTORE_DATABASE_URL }}"
    )
    rendered = yaml.safe_dump(workflow)
    assert "MIGRATION_DATABASE_URL" in rendered
    assert "postgresql+asyncpg://" in rendered
    assert "PG_DUMP_DATABASE_URL" in rendered
    assert "PG_RESTORE_DATABASE_URL" in rendered
    assert "postgresql://" in rendered
    assert "pg_dump --format=custom --no-owner --no-acl" in rendered
    assert '"$PG_DUMP_DATABASE_URL"' in rendered
    assert "alembic -c apps/api/alembic.ini current" in rendered
    assert "alembic -c apps/api/alembic.ini heads" in rendered
    assert "alembic" in rendered
    restore_command = str(restore_step["run"])
    assert "pg_restore --clean --if-exists --no-owner --no-acl" in restore_command
    assert '"$PG_RESTORE_DATABASE_URL"' in restore_command


def test_ci_workflow_builds_tests_and_deploys_prebuilt_vercel_artifacts() -> None:
    # Given: the deployment CI workflow for Vercel preview and production.
    workflow = _workflow("ci.yml")
    triggers = _mapping(workflow["on"])
    permissions = _mapping(workflow["permissions"])
    ci_job = _job(workflow, "ci")
    preview_job = _job(workflow, "deploy-preview")
    production_job = _job(workflow, "deploy-production")

    # When/Then: it uses pinned tooling, test gates, and prebuilt Vercel deploys.
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    assert permissions == {"contents": "read"}
    assert ci_job["timeout-minutes"] == 15
    assert preview_job["environment"] == "preview-deploy"
    assert production_job["environment"] == "production-deploy"
    assert preview_job["needs"] == "ci"
    assert production_job["needs"] == "ci"
    rendered = yaml.safe_dump(workflow)
    assert "corepack prepare pnpm@9.15.4 --activate" in rendered
    assert "npm install --global vercel@51.7.0" in rendered
    assert "vercel pull --yes" in rendered
    assert "vercel build" in rendered
    assert "pnpm --filter @prediction-market/web test" in rendered
    assert "vercel deploy --prebuilt" in rendered
    assert "--prod" in rendered
    assert "VERCEL_TOKEN" in rendered
    assert "SERVICE_ROLE" not in rendered.upper()
    assert "SUPABASE_SERVICE" not in rendered.upper()


def test_vercel_monorepo_deploys_from_repository_root_by_matrix_project() -> None:
    # Given: isolated preview and production matrix jobs for both Vercel projects.
    workflow = _workflow("ci.yml")

    # When/Then: project IDs select the target while every Vercel command and
    # its Build Output API assertion execute at the repository root.
    expected_projects = {
        ("api", "VERCEL_API_PROJECT_ID"),
        ("web", "VERCEL_WEB_PROJECT_ID"),
    }
    for job_name in ("deploy-preview", "deploy-production"):
        job = _job(workflow, job_name)
        strategy = _mapping(job["strategy"])
        matrix = _mapping(strategy["matrix"])
        include = matrix["include"]
        assert isinstance(include, list)
        projects = {
            (str(item["app"]), str(item["project_secret"]))
            for value in include
            if isinstance(value, dict)
            for item in (_mapping(value),)
        }
        assert projects == expected_projects

        vercel_steps = tuple(
            step
            for value in _steps(job)
            if isinstance(value, dict)
            for step in (_mapping(value),)
            if "vercel " in str(step.get("run", ""))
            or ".vercel/output" in str(step.get("run", ""))
        )
        assert len(vercel_steps) == 4
        for step in vercel_steps:
            assert "working-directory" not in step
        output_step = next(
            step for step in vercel_steps if ".vercel/output" in str(step["run"])
        )
        assert output_step["run"] == "test -d .vercel/output"
        for step in vercel_steps:
            if "vercel " not in str(step["run"]):
                continue
            environment = _mapping(step["env"])
            assert environment["VERCEL_ORG_ID"] == "${{ secrets.VERCEL_ORG_ID }}"
            assert environment["VERCEL_PROJECT_ID"] == (
                "${{ secrets[matrix.project_secret] }}"
            )


def test_all_workflows_use_immutable_actions_and_exact_uv() -> None:
    # Given: every workflow that can run in GitHub-hosted infrastructure.
    workflows = tuple(
        _workflow(name)
        for name in (
            "ci.yml",
            "collect.yml",
            "verify.yml",
            "migrate.yml",
        )
    )

    # When/Then: no floating action major or unpinned uv installer remains.
    rendered = "\n".join(yaml.safe_dump(workflow) for workflow in workflows)
    assert "@v4" not in rendered
    assert "@v6" not in rendered
    for pinned_action in PINNED_ACTIONS:
        if pinned_action.startswith("actions/setup-node"):
            assert pinned_action in rendered
            continue
        assert pinned_action in rendered
    assert rendered.count("version: 0.5.30") >= 4


def test_private_scheduled_budget_is_ineligible_for_the_required_cadence() -> None:
    # Given: the exact collection and verifier schedules in a 31-day month.
    collect_minutes = _int(_job(_workflow("collect.yml"), "collect")["timeout-minutes"])
    verify_minutes = _int(_job(_workflow("verify.yml"), "verify")["timeout-minutes"])

    # When/Then: the required cadence exceeds private GitHub Free minutes,
    # so the public-repository job gate must remain part of the workflow.
    monthly_minutes = (collect_minutes * 8 * 31) + (verify_minutes * 96 * 31)
    assert monthly_minutes == 10416
    assert monthly_minutes > 2000
    assert "github.event.repository.private == false" in str(
        _job(_workflow("verify.yml"), "verify")["if"]
    )
