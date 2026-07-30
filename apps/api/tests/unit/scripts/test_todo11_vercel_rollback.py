# pyright: reportAny=false, reportArgumentType=false
# pyright: reportUnannotatedClassAttribute=false, reportUnusedCallResult=false

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from scripts.release_rollback_models import (
    DatabaseRollbackState,
    DeploymentState,
    HealthState,
    MatrixBHealthInput,
    RollbackFinalizeInput,
)
from scripts.release_rollback_validation import (
    plan_rollback_finalize,
    validate_matrix_b_health,
)
from scripts.release_vercel_commands import run_vercel_operation
from scripts.release_vercel_compat import (
    CompatibilityDatabaseState,
    CompatibilityStateInput,
    validate_compat_state,
)
from scripts.release_vercel_handlers import run_vercel_deploy, run_vercel_prestate
from scripts.release_vercel_models import (
    CLI_VERSION,
    ORG_ID_ENV,
    TEAM_SLUG,
    TOKEN_ENV,
    ChildCommand,
    ChildResult,
    ReleaseHoldError,
    VercelOperation,
    VercelPrestateRequest,
    seal_receipt,
)
from scripts.release_vercel_retention import (
    AliasRetentionObservation,
    build_alias_retention_proof,
    parse_utc_timestamp,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

SHA = "a" * 40
PLAN = "b" * 64
ACTIVATION = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
API_URL = "https://api-deployment.vercel.app"
TEST_ROOT = Path("release-test-repository")


class RecordingRunner:
    def __init__(self, results: Mapping[str, ChildResult]) -> None:
        self.results = results
        self.commands: list[ChildCommand] = []

    def execute(self, command: ChildCommand) -> ChildResult:
        self.commands.append(command)
        return self.results.get(command.stage, ChildResult(0))


def receipt(
    command: str,
    *,
    predecessor: str | None = None,
    accepted: bool = True,
    **extra: object,
) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": 1,
        "command": command,
        "reviewed_sha": SHA,
        "approved_plan_sha256": PLAN,
        "activation_nonce": str(ACTIVATION),
        "accepted": accepted,
        **extra,
    }
    if predecessor is not None:
        body["predecessor_receipt_sha256"] = predecessor
    return seal_receipt(body)


def operation(tmp_path: Path, **changes: object) -> VercelOperation:
    root = tmp_path / "vercel" / "matrix-b-rebuild" / "api" / "attempt-1"
    values: dict[str, object] = {
        "operation": "matrix-b-rebuild",
        "attempt": 1,
        "attempt_root": root,
        "repository_root": tmp_path,
        "project_kind": "api",
        "team_slug": TEAM_SLUG,
        "org_id_env": ORG_ID_ENV,
        "project_name": "prediction-monitor-api",
        "project_id_env": "VERCEL_API_PROJECT_ID",
        "token_env": TOKEN_ENV,
        "target_sha": SHA,
        "protected_ref": "origin/main",
        "expected_sha": SHA,
        "expected_plan_sha256": PLAN,
        "activation_nonce": ACTIVATION,
        "cli_version": CLI_VERSION,
        "predecessor_receipt": receipt("binding-restore-verified"),
    }
    values.update(changes)
    return VercelOperation(**values)  # type: ignore[arg-type]


def inspect_json(kind: str = "api", source_sha: str = SHA) -> str:
    project = f"prediction-monitor-{kind}"
    return json.dumps(
        {
            "id": "dpl_exact",
            "url": API_URL,
            "name": project,
            "team": TEAM_SLUG,
            "target": "production",
            "readyState": "READY",
            "meta": {"githubCommitSha": source_sha},
        }
    )


def successful_runner() -> RecordingRunner:
    return RecordingRunner(
        {
            "target-sha": ChildResult(0, SHA),
            "protected-sha": ChildResult(0, SHA),
            "deploy": ChildResult(0, API_URL),
            "inspect": ChildResult(0, inspect_json()),
            "health": ChildResult(
                0,
                json.dumps(
                    {
                        "status": "ok",
                        "reviewed_sha": SHA,
                        "database_revision": "20260727_0010",
                        "manifold_enabled": False,
                    }
                ),
            ),
        }
    )


def test_restore_invokes_exact_pinned_pipeline_once_without_secret_argv() -> None:
    runner = successful_runner()
    result = run_vercel_operation(operation(TEST_ROOT), runner)

    assert result["accepted"] is True
    by_stage = {command.stage: command for command in runner.commands}
    assert [command.stage for command in runner.commands] == [
        "target-sha",
        "protected-sha",
        "reachable",
        "worktree-add",
        "pull",
        "build",
        "deploy",
        "inspect",
        "alias",
        "health",
        "worktree-remove",
    ]
    assert by_stage["worktree-add"].argv[-2:] == (
        str(operation(TEST_ROOT).attempt_root / "worktree"),
        SHA,
    )
    assert by_stage["pull"].argv == (
        "npx",
        "--yes",
        "vercel@51.7.0",
        "pull",
        "--environment=production",
        "--scope",
        TEAM_SLUG,
        "--yes",
    )
    assert by_stage["deploy"].argv[3:7] == (
        "deploy",
        "--prebuilt",
        "--prod",
        "--scope",
    )
    assert by_stage["alias"].argv[3:7] == (
        "alias",
        "set",
        API_URL,
        "prediction-monitor-api.vercel.app",
    )
    argv_text = "\n".join(arg for command in runner.commands for arg in command.argv)
    assert "redeploy" not in argv_text
    assert "promote" not in argv_text
    assert "rollback" not in argv_text
    assert TOKEN_ENV not in argv_text


def test_prestate_is_one_read_only_inspect_and_no_network_client() -> None:
    runner = RecordingRunner({"inspect": ChildResult(0, inspect_json())})
    request = VercelPrestateRequest(
        repository_root=TEST_ROOT,
        project_kind="api",
        team_slug=TEAM_SLUG,
        org_id_env=ORG_ID_ENV,
        project_name="prediction-monitor-api",
        project_id_env="VERCEL_API_PROJECT_ID",
        token_env=TOKEN_ENV,
        protected_ref="origin/main",
        expected_sha=SHA,
        expected_plan_sha256=PLAN,
        activation_nonce=ACTIVATION,
        cli_version=CLI_VERSION,
    )
    assert run_vercel_prestate(request, runner)["accepted"] is True
    assert [command.argv for command in runner.commands] == [
        (
            "npx",
            "--yes",
            "vercel@51.7.0",
            "inspect",
            "prediction-monitor-api.vercel.app",
            "--scope",
            TEAM_SLUG,
            "--json",
        )
    ]


@pytest.mark.parametrize(
    ("change", "value", "reason"),
    [
        ("team_slug", "foreign", "wrong_team"),
        ("project_name", "foreign", "wrong_project"),
        ("project_id_env", "WRONG_ID", "wrong_project_id_env"),
        ("target_sha", "c" * 40, "wrong_target_sha"),
        ("protected_ref", "origin/feature", "wrong_protected_ref"),
        ("cli_version", "latest", "unpinned_vercel_cli"),
    ],
)
def test_identity_and_sha_fail_before_any_child(
    change: str,
    value: object,
    reason: str,
) -> None:
    runner = RecordingRunner({})
    with pytest.raises(ReleaseHoldError, match=reason):
        run_vercel_operation(operation(TEST_ROOT, **{change: value}), runner)
    assert runner.commands == []


def test_unreachable_sha_stops_before_worktree() -> None:
    runner = successful_runner()
    runner.results = {**runner.results, "reachable": ChildResult(1)}
    with pytest.raises(ReleaseHoldError, match="target_sha_unreachable"):
        run_vercel_operation(operation(TEST_ROOT), runner)
    assert [item.stage for item in runner.commands][-1] == "reachable"


def test_alias_failure_is_terminal_and_has_no_hidden_retry() -> None:
    runner = successful_runner()
    runner.results = {**runner.results, "alias": ChildResult(1, stderr="no")}
    result = run_vercel_operation(operation(TEST_ROOT), runner)
    assert result["accepted"] is False
    assert result["retry_permitted"] is True
    assert [item.stage for item in runner.commands].count("alias") == 1
    assert "health" not in [item.stage for item in runner.commands]
    assert [item.stage for item in runner.commands].count("worktree-add") == 1


def test_attempt_two_requires_exact_failed_attempt_one() -> None:
    first = operation(TEST_ROOT)
    failed = receipt(
        "vercel-restore",
        accepted=False,
        attempt=1,
        operation="matrix-b-rebuild",
        project_kind="api",
        terminal_for_attempt=True,
        retry_permitted=True,
    )
    second = replace(
        first,
        attempt=2,
        attempt_root=first.attempt_root.parent / "attempt-2",
        predecessor_receipt=failed,
    )
    runner = successful_runner()
    assert run_vercel_operation(second, runner)["accepted"] is True

    invalid = replace(second, predecessor_receipt=receipt("unrelated"))
    runner = RecordingRunner({})
    with pytest.raises(ReleaseHoldError, match="illegal_attempt_2_predecessor"):
        run_vercel_operation(invalid, runner)
    assert runner.commands == []


def test_split_compensation_rebuilds_only_captured_protected_prestate() -> None:
    old_sha = "d" * 40
    prestate = receipt(
        "vercel-prestate",
        project_kind="api",
        protected_source_sha=old_sha,
    )
    request = operation(
        TEST_ROOT,
        operation="split-compensation",
        attempt_root=(
            TEST_ROOT / "vercel" / "split-compensation" / "api" / "attempt-1"
        ),
        target_sha=old_sha,
        deployment_prestate=prestate,
    )
    runner = successful_runner()
    runner.results = {
        **runner.results,
        "target-sha": ChildResult(0, old_sha),
        "inspect": ChildResult(0, inspect_json(source_sha=old_sha)),
        "health": ChildResult(
            0,
            json.dumps(
                {
                    "status": "ok",
                    "reviewed_sha": old_sha,
                    "database_revision": "20260727_0010",
                    "manifold_enabled": False,
                }
            ),
        ),
    }
    result = run_vercel_operation(request, runner)
    assert result["accepted"] is True
    assert result["source_sha"] == old_sha
    assert result["state_after"] == "deployment_prestate_restored"

    with pytest.raises(ReleaseHoldError, match="wrong_split_prestate"):
        run_vercel_operation(
            replace(
                request,
                deployment_prestate=receipt(
                    "vercel-prestate",
                    project_kind="api",
                    protected_source_sha="e" * 40,
                ),
            ),
            RecordingRunner({}),
        )


def test_compat_alias_uses_existing_deployment_without_rebuild() -> None:
    initial = receipt(
        "vercel-deploy",
        operation="initial-deploy",
        project_kind="api",
        deployment_id="dpl_exact",
        deployment_url=API_URL,
        source_sha=SHA,
        ready_state="READY",
        environment="production",
    )
    request = operation(
        TEST_ROOT,
        operation="compat-alias",
        attempt_root=(TEST_ROOT / "vercel" / "compat-alias" / "api" / "attempt-1"),
        target_deployment_receipt=initial,
    )
    runner = RecordingRunner(
        {
            "alias-ls": ChildResult(
                0,
                json.dumps(
                    {
                        "aliases": [
                            {
                                "alias": (
                                    "prediction-monitor-api-fresh-search-compat"
                                    ".vercel.app"
                                ),
                                "deploymentId": "dpl_exact",
                            }
                        ]
                    }
                ),
            ),
            "inspect": ChildResult(0, inspect_json()),
        }
    )
    result = run_vercel_deploy(request, runner)
    assert result["accepted"] is True
    assert result["operation"] == "compat-alias"
    assert result["alias"] == ("prediction-monitor-api-fresh-search-compat.vercel.app")
    assert [item.stage for item in runner.commands] == [
        "alias",
        "alias-ls",
        "inspect",
    ]
    assert runner.commands[0].argv == (
        "npx",
        "--yes",
        "vercel@51.7.0",
        "alias",
        "set",
        API_URL,
        "prediction-monitor-api-fresh-search-compat.vercel.app",
        "--scope",
        TEAM_SLUG,
    )
    assert runner.commands[1].argv == (
        "npx",
        "--yes",
        "vercel@51.7.0",
        "alias",
        "ls",
        "--scope",
        TEAM_SLUG,
        "--json",
    )
    assert runner.commands[2].argv == (
        "npx",
        "--yes",
        "vercel@51.7.0",
        "inspect",
        "prediction-monitor-api-fresh-search-compat.vercel.app",
        "--scope",
        TEAM_SLUG,
        "--json",
    )
    assert result["command"] == "vercel-deploy"
    assert result["deployment_id"] == "dpl_exact"
    assert result["source_sha"] == SHA
    assert result["state_after"] == "compatibility_deploying"


def test_compat_alias_failure_is_attempt_indexed_and_attempt_two_only() -> None:
    initial = receipt(
        "vercel-deploy",
        operation="initial-deploy",
        project_kind="api",
        deployment_id="dpl_exact",
        deployment_url=API_URL,
        source_sha=SHA,
        ready_state="READY",
        environment="production",
    )
    first = operation(
        TEST_ROOT,
        operation="compat-alias",
        attempt_root=(TEST_ROOT / "vercel" / "compat-alias" / "api" / "attempt-1"),
        target_deployment_receipt=initial,
    )
    failed_runner = RecordingRunner(
        {
            "alias-ls": ChildResult(0, json.dumps({"aliases": []})),
            "inspect": ChildResult(0, inspect_json()),
        }
    )
    failed = run_vercel_deploy(first, failed_runner)
    assert failed["accepted"] is False
    assert failed["terminal_for_attempt"] is True
    assert failed["retry_permitted"] is True
    assert failed["failed_stage"] == "verification"
    assert [item.stage for item in failed_runner.commands].count("alias") == 1

    second = replace(
        first,
        attempt=2,
        attempt_root=first.attempt_root.parent / "attempt-2",
        predecessor_receipt=failed,
    )
    success_runner = RecordingRunner(
        {
            "alias-ls": ChildResult(
                0,
                json.dumps(
                    {
                        "aliases": [
                            {
                                "alias": second.alias,
                                "deploymentId": "dpl_exact",
                            }
                        ]
                    }
                ),
            ),
            "inspect": ChildResult(0, inspect_json()),
        }
    )
    assert run_vercel_deploy(second, success_runner)["accepted"] is True

    with pytest.raises(
        ReleaseHoldError,
        match="illegal_attempt_2_predecessor",
    ):
        run_vercel_deploy(
            replace(second, predecessor_receipt=receipt("unrelated")),
            RecordingRunner({}),
        )


def db_state(**changes: object) -> DatabaseRollbackState:
    values: dict[str, object] = {
        "revision": "20260727_0010",
        "latest_transition": "restore_writing",
        "latest_transition_id": 17,
        "manifold_enabled": False,
        "active_authorization_id": None,
        "current_budget_id": None,
        "current_binding_id": None,
        "current_cadence_id": None,
        "original_dcinside_binding_sha256": "c" * 64,
        "current_dcinside_binding_sha256": "c" * 64,
        "zero_provider_binding": True,
    }
    values.update(changes)
    return DatabaseRollbackState(**values)  # type: ignore[arg-type]


def deployment(kind: str, **changes: object) -> DeploymentState:
    project = f"prediction-monitor-{kind}"
    values: dict[str, object] = {
        "project_kind": kind,
        "project_name": project,
        "team_slug": TEAM_SLUG,
        "source_sha": SHA,
        "ready_state": "READY",
        "environment": "production",
        "alias": f"{project}.vercel.app",
        "alias_assigned": True,
    }
    values.update(changes)
    return DeploymentState(**values)  # type: ignore[arg-type]


def matrix_input(**changes: object) -> MatrixBHealthInput:
    downgrade = receipt("migrate-0011-to-0010")
    binding = receipt(
        "binding-restore-verified",
        predecessor=str(downgrade["receipt_sha256"]),
    )
    api = receipt("vercel-restore", predecessor=str(binding["receipt_sha256"]))
    web = receipt("vercel-restore", predecessor=str(api["receipt_sha256"]))
    values: dict[str, object] = {
        "database": db_state(),
        "api": deployment("api"),
        "web": deployment("web"),
        "health": HealthState(
            api_ok=True,
            web_ok=True,
            dcinside_ok=True,
            dcinside_search_ok=True,
            manifold_results=0,
        ),
        "downgrade_receipt": downgrade,
        "binding_restore_receipt": binding,
        "api_receipt": api,
        "web_receipt": web,
        "expected_sha": SHA,
        "expected_plan_sha256": PLAN,
        "activation_nonce": ACTIVATION,
        "predecessor_receipt": web,
    }
    values.update(changes)
    return MatrixBHealthInput(**values)  # type: ignore[arg-type]


def test_matrix_b_health_leaves_restore_writing_and_finalizer_returns_cas_intent() -> (
    None
):
    request = matrix_input()
    health = validate_matrix_b_health(request)
    assert health["state_after"] == "restore_writing"
    chain = receipt(
        "materialize-chain",
        manifest="matrix-b-chain-manifest.json",
        expected_terminal_command="matrix-b-health",
        terminal_receipt_sha256=health["receipt_sha256"],
        branch_complete=True,
        node_count=6,
        extra_nodes=0,
    )
    final = RollbackFinalizeInput(
        incident_class="technical",
        database=request.database,
        api=request.api,
        web=request.web,
        health_receipt=health,
        matrix_b_chain=chain,
        expected_sha=SHA,
        expected_plan_sha256=PLAN,
        activation_nonce=ACTIVATION,
        predecessor_receipt=chain,
    )
    intent = plan_rollback_finalize(final)
    assert intent.expected_latest_transition_id == 17
    assert intent.expected_latest_transition == "restore_writing"
    assert intent.next_transition == "restored"
    assert intent.receipt_body["state_after"] == "restored"
    replay = plan_rollback_finalize(final)
    assert replay.receipt_body == intent.receipt_body

    with pytest.raises(ReleaseHoldError, match="not_restore_writing"):
        plan_rollback_finalize(
            replace(final, database=db_state(latest_transition="restored"))
        )


@pytest.mark.parametrize("incident", ["privacy", "authorization"])
def test_privacy_classes_can_never_finalize_restored(incident: str) -> None:
    request = matrix_input()
    health = validate_matrix_b_health(request)
    chain = receipt(
        "materialize-chain",
        manifest="matrix-b-chain-manifest.json",
        expected_terminal_command="matrix-b-health",
        terminal_receipt_sha256=health["receipt_sha256"],
        branch_complete=True,
        node_count=6,
    )
    final = RollbackFinalizeInput(
        incident_class=incident,  # type: ignore[arg-type]
        database=request.database,
        api=request.api,
        web=request.web,
        health_receipt=health,
        matrix_b_chain=chain,
        expected_sha=SHA,
        expected_plan_sha256=PLAN,
        activation_nonce=ACTIVATION,
        predecessor_receipt=chain,
    )
    with pytest.raises(
        ReleaseHoldError,
        match="privacy_incident_requires_privacy_verify",
    ):
        plan_rollback_finalize(final)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"database": db_state(manifold_enabled=True)}, "manifold_enabled"),
        ({"database": db_state(current_budget_id=ACTIVATION)}, "active_pointer"),
        (
            {"api": deployment("api", alias_assigned=False)},
            "alias_failure",
        ),
        (
            {"web": deployment("web", source_sha="d" * 40)},
            "wrong_deployment_sha",
        ),
    ],
)
def test_matrix_b_rejects_inertness_alias_and_sha_failures(
    changes: Mapping[str, object],
    reason: str,
) -> None:
    with pytest.raises(ReleaseHoldError, match=reason):
        validate_matrix_b_health(matrix_input(**changes))


def test_manifest_allows_only_one_or_failed_then_two_branches() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "release-gate"
        / "matrix-b-chain-manifest.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected = [
        "accepted-attempt-1",
        "failed-attempt-1-accepted-attempt-2",
    ]
    assert manifest["terminal_command"] == "matrix-b-health"
    assert all(step["allowed_branches"] == expected for step in manifest["steps"])
    assert manifest["reject_unselected_paths"] is True


def alias_retention_observation(
    *,
    kind: str,
    alias: str,
    deployment_id: str,
    observed_at: datetime,
) -> AliasRetentionObservation:
    return AliasRetentionObservation(
        project_kind=kind,
        alias=alias,
        deployment_id=deployment_id,
        project_name=f"prediction-monitor-{kind}",
        team_slug=TEAM_SLUG,
        source_sha=SHA,
        ready_state="READY",
        environment="production",
        evidence_source="vercel-alias-ls-inspect",
        observed_at=observed_at,
    )


def test_compat_state_requires_ordered_exact_sha_aliases_and_api_endpoints() -> None:
    anchor = datetime(2026, 7, 29, 6, tzinfo=UTC)
    db_now = datetime(2026, 7, 29, 5, tzinfo=UTC)
    api_alias = "prediction-monitor-api-fresh-search-compat.vercel.app"
    web_alias = "prediction-monitor-web-fresh-search-compat.vercel.app"
    api_receipt = receipt(
        "vercel-deploy",
        alias=api_alias,
        deployment_id="dpl_api",
        source_sha=SHA,
        project_name="prediction-monitor-api",
        team_slug=TEAM_SLUG,
    )
    web_receipt = receipt(
        "vercel-deploy",
        predecessor=str(api_receipt["receipt_sha256"]),
        alias=web_alias,
        deployment_id="dpl_web",
        source_sha=SHA,
        project_name="prediction-monitor-web",
        team_slug=TEAM_SLUG,
    )
    request = CompatibilityStateInput(
        database=CompatibilityDatabaseState(
            revision="20260727_0010",
            manifold_rows=0,
            manifold_enabled=False,
            active_pointer_count=0,
        ),
        api=deployment(
            "api",
            alias=api_alias,
        ),
        web=deployment(
            "web",
            alias=web_alias,
        ),
        health=HealthState(
            api_ok=True,
            web_ok=True,
            dcinside_ok=True,
            dcinside_search_ok=True,
            manifold_results=0,
        ),
        api_claim_endpoint_compatible=True,
        api_evidence_endpoint_compatible=True,
        api_receipt=api_receipt,
        web_receipt=web_receipt,
        expected_sha=SHA,
        expected_plan_sha256=PLAN,
        activation_nonce=ACTIVATION,
        predecessor_receipt=web_receipt,
        cadence_anchor_at=anchor,
        db_now=db_now,
        api_retention=build_alias_retention_proof(
            observation=alias_retention_observation(
                kind="api",
                alias=api_alias,
                deployment_id="dpl_api",
                observed_at=db_now,
            ),
            alias_receipt=api_receipt,
            cadence_anchor_at=anchor,
        ),
        web_retention=build_alias_retention_proof(
            observation=alias_retention_observation(
                kind="web",
                alias=web_alias,
                deployment_id="dpl_web",
                observed_at=db_now,
            ),
            alias_receipt=web_receipt,
            cadence_anchor_at=anchor,
        ),
    )
    result = validate_compat_state(request)
    assert result["command"] == "compat-state"
    assert result["accepted"] is True
    assert result["alias_retained_through"] == "2026-08-29T06:00:00Z"

    with pytest.raises(ReleaseHoldError, match="endpoint_missing"):
        validate_compat_state(replace(request, api_claim_endpoint_compatible=False))
    with pytest.raises(ReleaseHoldError, match="alias_failure"):
        validate_compat_state(
            replace(
                request,
                api=deployment(
                    "api",
                    alias=("prediction-monitor-api-fresh-search-compat.vercel.app"),
                    alias_assigned=False,
                ),
            )
        )


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("2026-07-29T06:00:00", "must_be_utc_aware"),
        ("2026-07-29T15:00:00+09:00", "must_be_utc_aware"),
        ("not-a-time", "cadence_anchor_at_invalid"),
    ],
)
def test_compat_anchor_requires_exact_aware_utc(value: str, reason: str) -> None:
    with pytest.raises(ReleaseHoldError, match=reason):
        parse_utc_timestamp(value, "cadence_anchor_at")


def test_retention_builder_requires_fresh_provider_observation_binding() -> None:
    anchor = datetime(2026, 7, 29, 6, tzinfo=UTC)
    observed_at = anchor - timedelta(hours=1)
    alias = "prediction-monitor-api-fresh-search-compat.vercel.app"
    alias_receipt = receipt(
        "vercel-deploy",
        alias=alias,
        deployment_id="dpl_api",
        source_sha=SHA,
        project_name="prediction-monitor-api",
        team_slug=TEAM_SLUG,
    )
    observation = alias_retention_observation(
        kind="api",
        alias=alias,
        deployment_id="dpl_api",
        observed_at=observed_at,
    )
    assert (
        build_alias_retention_proof(
            observation=observation,
            alias_receipt=alias_receipt,
            cadence_anchor_at=anchor,
        ).rechecked_at
        == observed_at
    )
    with pytest.raises(ReleaseHoldError, match="external_evidence"):
        build_alias_retention_proof(
            observation=replace(observation, team_slug="foreign"),
            alias_receipt=alias_receipt,
            cadence_anchor_at=anchor,
        )
    with pytest.raises(ReleaseHoldError, match="observation_mismatch"):
        build_alias_retention_proof(
            observation=replace(observation, deployment_id="dpl_foreign"),
            alias_receipt=alias_receipt,
            cadence_anchor_at=anchor,
        )


@pytest.mark.parametrize(
    ("proof_changes", "request_changes", "reason"),
    [
        (
            {"retained_through_delta": -timedelta(microseconds=1)},
            {},
            "retention_too_short",
        ),
        (
            {"evidence_source": "caller-supplied"},
            {},
            "external_evidence",
        ),
        (
            {"rechecked_at_delta": -timedelta(minutes=5)},
            {},
            "recheck_stale",
        ),
        (
            {"rechecked_at_delta": timedelta(microseconds=1)},
            {},
            "recheck_from_future",
        ),
        (
            {"renewal_delta": timedelta(seconds=1)},
            {},
            "renewal_schedule_drift",
        ),
        (
            {},
            {"db_now_at_expiry": True},
            "retention_expired",
        ),
        (
            {},
            {"anchor_future_delta": timedelta(hours=4, microseconds=1)},
            "external_future",
        ),
    ],
)
def test_compat_retention_rejects_short_external_past_and_stale_proofs(
    proof_changes: Mapping[str, object],
    request_changes: Mapping[str, object],
    reason: str,
) -> None:
    request = compat_retention_request()
    proof = request.api_retention
    retained_delta = proof_changes.get(
        "retained_through_delta",
        timedelta(0),
    )
    rechecked_delta = proof_changes.get(
        "rechecked_at_delta",
        timedelta(0),
    )
    renewal_delta = proof_changes.get(
        "renewal_delta",
        timedelta(0),
    )
    assert isinstance(retained_delta, timedelta)
    assert isinstance(rechecked_delta, timedelta)
    assert isinstance(renewal_delta, timedelta)
    retained = proof.retained_through + retained_delta
    rechecked = proof.rechecked_at + rechecked_delta
    renewal = proof.renewal_recheck_at + renewal_delta
    changed_proof = replace(
        proof,
        evidence_source=str(
            proof_changes.get("evidence_source", proof.evidence_source)
        ),
        retained_through=retained,
        rechecked_at=rechecked,
        renewal_recheck_at=renewal,
    )
    changed_request = replace(request, api_retention=changed_proof)
    if request_changes.get("db_now_at_expiry") is True:
        changed_request = replace(
            changed_request,
            db_now=proof.retained_through,
            api_retention=replace(
                changed_proof,
                rechecked_at=proof.retained_through,
            ),
            web_retention=replace(
                request.web_retention,
                rechecked_at=proof.retained_through,
            ),
        )
    anchor_delta = request_changes.get("anchor_future_delta")
    if isinstance(anchor_delta, timedelta):
        changed_request = replace(
            changed_request,
            cadence_anchor_at=request.db_now + anchor_delta,
        )
    with pytest.raises(ReleaseHoldError, match=reason):
        validate_compat_state(changed_request)


def test_compat_renewal_requires_fresh_recheck_at_due_boundary() -> None:
    request = compat_retention_request()
    renewal_at = request.api_retention.renewal_recheck_at
    due = replace(
        request,
        db_now=renewal_at,
        api_retention=replace(request.api_retention, rechecked_at=renewal_at),
        web_retention=replace(request.web_retention, rechecked_at=renewal_at),
    )
    result = validate_compat_state(due)
    assert result["renewal_recheck_satisfied"] is True

    stale_for_renewal = replace(
        due,
        api_retention=replace(
            due.api_retention,
            rechecked_at=renewal_at - timedelta(seconds=1),
        ),
    )
    with pytest.raises(ReleaseHoldError, match="renewal_recheck_missing"):
        validate_compat_state(stale_for_renewal)


def compat_retention_request() -> CompatibilityStateInput:
    anchor = datetime(2026, 7, 29, 6, tzinfo=UTC)
    db_now = anchor - timedelta(hours=1)
    api_alias = "prediction-monitor-api-fresh-search-compat.vercel.app"
    web_alias = "prediction-monitor-web-fresh-search-compat.vercel.app"
    api_receipt = receipt(
        "vercel-deploy",
        alias=api_alias,
        deployment_id="dpl_api",
        source_sha=SHA,
        project_name="prediction-monitor-api",
        team_slug=TEAM_SLUG,
    )
    web_receipt = receipt(
        "vercel-deploy",
        predecessor=str(api_receipt["receipt_sha256"]),
        alias=web_alias,
        deployment_id="dpl_web",
        source_sha=SHA,
        project_name="prediction-monitor-web",
        team_slug=TEAM_SLUG,
    )
    return CompatibilityStateInput(
        database=CompatibilityDatabaseState(
            revision="20260727_0010",
            manifold_rows=0,
            manifold_enabled=False,
            active_pointer_count=0,
        ),
        api=deployment("api", alias=api_alias),
        web=deployment("web", alias=web_alias),
        health=HealthState(
            api_ok=True,
            web_ok=True,
            dcinside_ok=True,
            dcinside_search_ok=True,
            manifold_results=0,
        ),
        api_claim_endpoint_compatible=True,
        api_evidence_endpoint_compatible=True,
        api_receipt=api_receipt,
        web_receipt=web_receipt,
        expected_sha=SHA,
        expected_plan_sha256=PLAN,
        activation_nonce=ACTIVATION,
        predecessor_receipt=web_receipt,
        cadence_anchor_at=anchor,
        db_now=db_now,
        api_retention=build_alias_retention_proof(
            observation=alias_retention_observation(
                kind="api",
                alias=api_alias,
                deployment_id="dpl_api",
                observed_at=db_now,
            ),
            alias_receipt=api_receipt,
            cadence_anchor_at=anchor,
        ),
        web_retention=build_alias_retention_proof(
            observation=alias_retention_observation(
                kind="web",
                alias=web_alias,
                deployment_id="dpl_web",
                observed_at=db_now,
            ),
            alias_receipt=web_receipt,
            cadence_anchor_at=anchor,
        ),
    )
