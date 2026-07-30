"""Snapshot the sole executable Todo 11 release-gate surface."""

# pyright: reportAny=false
# ruff: noqa: TC003

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(ROOT / "apps" / "api"))

from scripts.local_qa_manifest import GATE_PREFIX, ordered_commands  # noqa: E402
from scripts.release_gate_cli_parser import (  # noqa: E402
    COMMANDS,
    parse_args,
    parser,
)
from scripts.release_gate_cli_runtime import default_handlers, main  # noqa: E402
from scripts.release_gate_cli_vercel import deploy, restore  # noqa: E402

SCRIPT = ROOT / "apps" / "api" / "scripts" / "fresh_search_release_gate.py"
EXPECTED_COMMANDS = (
    "local-db", "code-quality", "no-spend-preflight", "bootstrap-dispatch",
    "bootstrap-select", "bootstrap-verify", "attest",
    "attestation-secret-upload", "canonical-hash", "evidence-import",
    "evidence-join", "recover-operation-receipt", "dispatch-reserve",
    "dispatch-workflow", "select-run", "verify-receipt", "materialize-chain",
    "activate", "vercel-prestate", "vercel-deploy", "vercel-restore",
    "rollback-finalize", "compat-state", "matrix-b-health", "production",
    "cadence", "acceptance-input-manifest", "acceptance-capture",
    "acceptance-refresh", "privacy-contain", "privacy-purge", "privacy-verify",
    "final-lane", "final-fan-in", "aggregate", "secret-static-scan",
    "plan-compliance", "scope-fidelity", "links",
)


def test_help_snapshots_every_normative_command_choice() -> None:
    environment = os.environ.copy()
    _ = environment.pop("PYTHONPATH", None)
    completed = subprocess.run(  # noqa: S603
        (sys.executable, str(SCRIPT), "--help"),
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert COMMANDS == EXPECTED_COMMANDS
    for command in EXPECTED_COMMANDS:
        assert command in completed.stdout


def test_every_registered_command_has_its_own_help_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    release_parser = parser()

    for command in COMMANDS:
        with pytest.raises(SystemExit) as stopped:
            _ = release_parser.parse_args([command, "--help"])
        assert stopped.value.code == 0
        assert command in capsys.readouterr().out


def test_every_os_manifest_gate_argv_has_one_exact_prefix(tmp_path: Path) -> None:
    commands = ordered_commands(tmp_path, "a" * 40, "b" * 40, "QA_URL")
    gates = commands[8:10] + commands[16:20]

    for argv in gates:
        assert argv[: len(GATE_PREFIX)] == GATE_PREFIX
        assert argv.count("apps/api/scripts/fresh_search_release_gate.py") == 1
        assert "..." not in argv
        assert "…" not in argv
        parsed = parse_args(argv[len(GATE_PREFIX) :])
        assert parsed.command in EXPECTED_COMMANDS


def test_exact_final_prefix_commands_parse_without_fragments() -> None:
    common = [
        "--database-url-env", "DB_URL", "--expected-sha", "a" * 40,
        "--expected-plan-sha256", "b" * 64, "--activation-nonce",
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    ]
    commands = (
        [
            *GATE_PREFIX, "final-lane", *common, "--lane", "F1",
            "--report", "f1.md",
            "--production-result", "production.json", "--predecessor-receipt",
            "production.json", "--json-out", "f1.json",
        ],
        [
            *GATE_PREFIX, "final-fan-in", *common, "--parent", "production.json",
            "--branch", "f1.json", "--branch", "f2.json", "--branch", "f3.json",
            "--expected-branches", "F1,F2,F3", "--predecessor-receipt",
            "production.json", "--json-out", "fan-in.json",
        ],
        [
            *GATE_PREFIX, "aggregate", *common,
            "--predecessor-receipt", "f4.json",
            "--fan-in", "fan-in.json", "--f4", "f4.json", "--cadence",
            "cadence.json", "--json-out", "status.json",
        ],
    )

    assert all(tuple(argv[: len(GATE_PREFIX)]) == GATE_PREFIX for argv in commands)
    assert [parse_args(argv[len(GATE_PREFIX) :]).command for argv in commands] == [
        "final-lane", "final-fan-in", "aggregate"
    ]


def test_injected_handler_executes_without_external_adapter() -> None:
    observed: list[str] = []

    def handler(args: argparse.Namespace) -> int:
        observed.append(str(args.input))
        return 17

    result = main(
        ["canonical-hash", "--input", "leaf.json", "--json-out", "hash.json"],
        handlers={"canonical-hash": handler},
    )

    assert result == 17
    assert observed == ["leaf.json"]


def test_every_normative_command_has_a_concrete_default_handler() -> None:
    assert set(default_handlers()) == set(EXPECTED_COMMANDS)


@pytest.mark.parametrize(
    ("handler", "command", "operation", "expected"),
    [
        (
            deploy,
            "vercel-deploy",
            "compat-alias",
            "vercel_target_deployment_contract_invalid",
        ),
        (
            restore,
            "vercel-restore",
            "split-compensation",
            "vercel_deployment_prestate_contract_invalid",
        ),
    ],
)
def test_vercel_special_operations_require_their_distinct_evidence(
    handler: Callable[[argparse.Namespace], int],
    command: str,
    operation: str,
    expected: str,
) -> None:
    args = argparse.Namespace(
        command=command,
        operation=operation,
        target_sha=None,
        expected_sha="a" * 40,
        target_deployment_receipt=None,
        deployment_prestate=None,
    )

    with pytest.raises(ValueError, match=expected):
        _ = handler(args)


def test_vercel_special_operation_argv_carries_distinct_evidence() -> None:
    common = [
        "--database-url-env", "DB_URL",
        "--expected-sha", "a" * 40,
        "--expected-plan-sha256", "b" * 64,
        "--activation-nonce", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "--predecessor-receipt", "previous.json",
        "--attempt", "1",
        "--project-kind", "api",
        "--team-slug", "63amg0010-5358-projects",
        "--org-id-env", "VERCEL_ORG_ID",
        "--project-name", "prediction-monitor-api",
        "--project-id-env", "VERCEL_API_PROJECT_ID",
        "--token-env", "VERCEL_TOKEN",
        "--protected-ref", "origin/main",
        "--cli-version", "51.7.0",
        "--json-out", "verified.json",
    ]
    alias = parse_args([
        "vercel-deploy", *common,
        "--operation", "compat-alias",
        "--attempt-root", "vercel/compat-alias/api/attempt-1",
        "--target-deployment-receipt", "initial-api.json",
    ])
    split = parse_args([
        "vercel-restore", *common,
        "--operation", "split-compensation",
        "--attempt-root", "vercel/split-compensation/api/attempt-1",
        "--target-sha", "c" * 40,
        "--deployment-prestate", "deployment-prestate.json",
    ])

    assert alias.target_deployment_receipt == "initial-api.json"
    assert split.deployment_prestate == "deployment-prestate.json"
