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
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
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


def _named_steps(job: dict[str, JsonValue]) -> dict[str, dict[str, JsonValue]]:
    return {
        str(step["name"]): step
        for value in _steps(job)
        if isinstance(value, dict)
        for step in (_mapping(value),)
        if "name" in step
    }


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
    collect_step = next(
        step
        for value in _steps(job)
        if isinstance(value, dict)
        for step in (_mapping(value),)
        if step.get("name") == "Collect through the scoped API"
    )
    assert collect_step["working-directory"] == "apps/api"
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


def test_migration_workflow_is_reviewed_revision_only_and_never_restores() -> None:  # noqa: PLR0915
    # Given: the only workflow allowed a migration database connection.
    workflow = _workflow("migrate.yml")
    triggers = _mapping(workflow["on"])
    job = _job(workflow, "migrate")

    # When/Then: no schedule exists and the immutable dispatch contract is exact.
    assert set(triggers) == {"workflow_dispatch"}
    assert job["environment"] == "production-migration"
    assert job["timeout-minutes"] == 10
    assert workflow["run-name"] == (
        "migrate-${{ inputs.operation }}-${{ inputs.revision }}-"
        "${{ inputs.dispatch_nonce }}-attempt-${{ inputs.attempt }}"
    )
    inputs = _mapping(_mapping(triggers["workflow_dispatch"])["inputs"])
    assert set(inputs) == {
        "operation",
        "revision",
        "attempt",
        "expected_commit_sha",
        "confirm",
        "activation_nonce",
        "dispatch_nonce",
        "expected_plan_sha256",
        "review_root_sha256",
        "review_root_b64",
        "no_spend_receipt_sha256",
        "no_spend_receipt_b64",
        "attempt1_failed_receipt_sha256",
        "attempt1_failed_receipt_b64",
        "attestation_run_id",
        "attestation_generation",
        "attestation_dispatch_nonce",
        "attestation_sha256",
        "reservation_sha256",
    }
    named_steps = _named_steps(job)
    dump_step = named_steps["Export pre-migration backup"]
    assert _mapping(dump_step["env"])["PG_DUMP_DATABASE_URL"] == (
        "${{ secrets.PG_DUMP_DATABASE_URL }}"
    )
    rendered = yaml.safe_dump(workflow)
    assert all(
        value in rendered
        for value in (
            "MIGRATION_DATABASE_URL",
            "PG_DUMP_DATABASE_URL",
            "PG_RESTORE_DATABASE_URL",
        )
    )
    install_step = str(named_steps["Install PostgreSQL client"]["run"])
    assert all(
        value in install_step
        for value in (
            "https://www.postgresql.org/media/keys/ACCC4CF8.asc",
            "https://apt.postgresql.org/pub/repos/apt",
            "postgresql-client-17",
            'export PATH="/usr/lib/postgresql/17/bin:$PATH"',
            "pg_dump --version",
            r"PostgreSQL\) 17\.",
        )
    )
    assert "pg_dump --format=custom --no-owner --no-acl" in rendered
    assert '"$PG_DUMP_DATABASE_URL"' in rendered
    validation_commands = "\n".join(
        str(named_steps[name]["run"])
        for name in (
            "Validate immutable reviewed dispatch",
            "Validate repository migration head",
            "Validate safe starting revision",
            "Verify failed operation remained at the safe revision",
        )
    )
    assert "-m scripts.migration_dispatch_validator validate-dispatch" in (
        validation_commands
    )
    assert "-m scripts.migration_dispatch_validator validate-heads" in (
        validation_commands
    )
    assert "-m scripts.migration_dispatch_validator validate-current" in (
        validation_commands
    )
    assert 'test "$(git rev-parse HEAD)" = "$EXPECTED_COMMIT_SHA"' in rendered
    assert 'test -z "$(git status --porcelain --untracked-files=no)"' in rendered
    operation_command = str(named_steps["Apply exact reviewed migration"]["run"])
    assert 'upgrade "$TARGET_REVISION"' in operation_command
    assert 'downgrade "$TARGET_REVISION"' in operation_command
    assert "upgrade head" not in rendered
    assert "pg_restore" not in rendered
    url_validation = named_steps[
        "Validate direct or session-mode database URLs without disclosure"
    ]
    assert "validate-database-urls" in str(url_validation["run"])
    assert set(_mapping(url_validation["env"])) == {
        "MIGRATION_DATABASE_URL",
        "PG_DUMP_DATABASE_URL",
        "PG_RESTORE_DATABASE_URL",
    }
    assert "Roll back failed migration" not in named_steps
    assert (
        named_steps["Verify failed operation remained at the safe revision"]["if"]
        == "${{ steps.migration.outcome == 'failure' }}"
    )
    reservation_check = str(named_steps["Verify claimed durable reservation"]["run"])
    assert "printf '%s\\n'" in reservation_check
    assert "--command=" not in reservation_check
    correction_export = str(named_steps["Export canonical correction receipt"]["run"])
    assert "release-correction-0010a" in correction_export
    assert "release-correction-0010b" in correction_export
    assert "release-correction-0010c" in correction_export
    assert "release-correction-0010d" in correction_export
    assert "release-correction-0010e" in correction_export
    assert "release-correction-0010f" in correction_export
    assert "--command=" not in correction_export
    assert "Encrypt and decrypt-test pre-migration backup" in named_steps
    assert "Upload encrypted recovery artifact" in named_steps
    assert "Verify claimed durable reservation" in named_steps
    attestation_command = str(
        named_steps["Verify completed 0011 attestation run identity"]["run"]
    )
    assert "gh run download" in attestation_command
    assert 'sha256sum "$attestation"' in attestation_command
    assert '= "$ATTESTATION_SHA256"' in attestation_command
    backup_command = str(
        named_steps["Encrypt and decrypt-test pre-migration backup"]["run"]
    )
    assert "MIGRATION_BACKUP_AGE_RECIPIENT" in backup_command
    assert "MIGRATION_BACKUP_AGE_IDENTITY" in backup_command
    assert "--decrypt" in backup_command
    failed_receipt_command = str(
        named_steps["Emit canonical bootstrap failed-attempt receipt"]["run"]
    )
    assert "jq -cS -n" in failed_receipt_command
    assert "terminal_for_attempt:true" in failed_receipt_command
    assert "retry_permitted:true" in failed_receipt_command
    assert all(
        fragment in failed_receipt_command
        for fragment in (
            "to_regclass('public.release_roots') IS NOT NULL",
            "platform::text='manifold'",
            "ledger_exists:$ledger_exists",
            "manifold_data_exists:$manifold_data_exists",
        )
    )
    assert "Upload canonical bootstrap failed-attempt receipt" in named_steps


def test_ci_workflow_builds_tests_and_deploys_vercel_artifacts() -> None:
    # Given: the deployment CI workflow for Vercel preview and production.
    workflow = _workflow("ci.yml")
    triggers = _mapping(workflow["on"])
    permissions = _mapping(workflow["permissions"])
    ci_job = _job(workflow, "ci")
    preview_job = _job(workflow, "deploy-preview")
    production_job = _job(workflow, "deploy-production")

    # When/Then: it uses pinned tooling, test gates, source deploys for the
    # Python API, and prebuilt deploys for the web app.
    assert set(triggers) == {"push", "pull_request", "workflow_dispatch"}
    assert permissions == {"contents": "read"}
    assert ci_job["timeout-minutes"] == 45
    assert preview_job["environment"] == "preview-deploy"
    assert production_job["environment"] == "production-deploy"
    assert preview_job["needs"] == "ci"
    assert production_job["needs"] == "ci"
    rendered = yaml.safe_dump(workflow)
    assert "corepack prepare pnpm@9.15.4 --activate" in rendered
    assert "npm install --global vercel@51.7.0" in rendered
    assert "vercel pull --yes" in rendered
    assert "vercel build" in rendered
    assert "apps/api/scripts/local_qa_orchestrator.py" in rendered
    assert "vercel deploy --prod --yes" in rendered
    assert "vercel deploy --prebuilt" in rendered
    assert "--prod" in rendered
    assert "VERCEL_TOKEN" in rendered
    assert "SERVICE_ROLE" not in rendered.upper()
    assert "SUPABASE_SERVICE" not in rendered.upper()


def test_vercel_monorepo_deploys_from_repository_root_by_matrix_project() -> None:
    # Given: isolated preview and production matrix jobs for both Vercel projects.
    workflow = _workflow("ci.yml")

    # When/Then: project IDs select the target while every Vercel command
    # executes at the repository root. The Python API is built remotely from
    # source; only the web app uses the local Build Output API artifact.
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
        assert len(vercel_steps) == 5
        for step in vercel_steps:
            assert "working-directory" not in step
        output_step = next(
            step for step in vercel_steps if ".vercel/output" in str(step["run"])
        )
        assert output_step["run"] == "test -d .vercel/output"
        assert output_step["if"] == "${{ matrix.app == 'web' }}"
        build_step = next(
            step for step in vercel_steps if str(step["run"]).startswith("vercel build")
        )
        assert build_step["if"] == "${{ matrix.app == 'web' }}"
        source_deploy_step = next(
            step
            for step in vercel_steps
            if str(step["run"]).startswith("vercel deploy")
            and "--prebuilt" not in str(step["run"])
        )
        assert source_deploy_step["if"] == "${{ matrix.app == 'api' }}"
        prebuilt_deploy_step = next(
            step
            for step in vercel_steps
            if str(step["run"]).startswith("vercel deploy --prebuilt")
        )
        assert prebuilt_deploy_step["if"] == "${{ matrix.app == 'web' }}"
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
            "activation-evidence.yml",
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
