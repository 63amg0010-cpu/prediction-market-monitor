"""Fail-fast, redacted Todo 11 orchestrator used by PowerShell and Git Bash."""

# pyright: reportAny=false
# ruff: noqa: E402, EM101

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Final, cast

ROOT: Final = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.local_qa_evidence import (
    command_nine_environment,
    sanitize_child_environment,
)
from scripts.local_qa_execution import (
    INJECTED_EXIT_CODE,
    EnvironmentScope,
    Executor,
    Options,
    console_safe_text,
    final_exit_code,
    injected_executor,
    invoke,
    redact_output,
    runtime_working_directory,
    unexpected_invocation,
    write_json,
)
from scripts.local_qa_manifest import (
    FAILURE_POINTS,
    Argv,
    dispose_argv,
    ordered_commands,
    provision_argv,
    runtime_argv,
)

COMMAND_NINE: Final = 9


def _real_executor(label: str, argv: Argv, env: dict[str, str]) -> int:
    capture = label == "command-08"
    completed: subprocess.CompletedProcess[str] = subprocess.run(  # noqa: S603
        runtime_argv(argv, platform=os.name),
        cwd=runtime_working_directory(label, argv, ROOT),
        env=env,
        capture_output=True,
        encoding="utf-8",
        errors="strict" if capture else "replace",
        check=False,
    )
    stdout = redact_output(completed.stdout, env)
    stderr = redact_output(completed.stderr, env)
    if not capture:
        _ = sys.stdout.write(console_safe_text(stdout, sys.stdout.encoding))
        _ = sys.stderr.write(console_safe_text(stderr, sys.stderr.encoding))
    if capture and completed.returncode == 0:
        heads = [line.strip() for line in stdout.splitlines() if line.strip()]
        if heads != ["20260803_0012 (head)"]:
            return 86
    return completed.returncode


_injected_executor = injected_executor


def run(options: Options, executor: Executor = _real_executor) -> int:
    """Provision, fail fast through 20 children, and always dispose."""
    _validate_options(options)
    options.attempt_dir.mkdir(parents=True, exist_ok=True)
    commands = ordered_commands(
        options.attempt_dir,
        options.base_sha,
        options.reviewed_sha,
        options.database_env,
    )
    child_env = os.environ.copy()
    database_secret = child_env.get(options.database_env, "")
    if not database_secret:
        raise RuntimeError("database_url_environment_empty")
    admin_secret = child_env.get(options.admin_env, "")
    sanitize_child_environment(child_env)
    environment_scope = EnvironmentScope(
        child_env,
        options.database_env,
        database_secret,
        options.admin_env,
        admin_secret,
    )
    events: list[dict[str, object]] = []
    exit_code = 0
    try:
        outcome = invoke(
            executor,
            "provision",
            provision_argv(
                options.attempt_dir,
                options.admin_env,
                options.database_env,
            ),
            environment_scope.for_label("provision"),
        )
        events.append(outcome.event)
        if outcome.exit_code != 0:
            exit_code = outcome.exit_code
        else:
            for number, argv in enumerate(commands, start=1):
                label = f"command-{number:02d}"
                invocation_env = environment_scope.for_label(label)
                try:
                    with command_nine_environment(
                        invocation_env,
                        options.attempt_dir,
                        options.reviewed_sha,
                        enabled=number == COMMAND_NINE,
                    ):
                        outcome = invoke(executor, label, argv, invocation_env)
                except Exception:  # noqa: BLE001 - failed event, then dispose.
                    outcome = unexpected_invocation(label)
                events.append(outcome.event)
                if outcome.exit_code != 0:
                    exit_code = outcome.exit_code
                    break
    finally:
        disposal = invoke(
            executor,
            "dispose",
            dispose_argv(
                options.attempt_dir,
                options.admin_env,
                options.database_env,
            ),
            environment_scope.for_label("dispose"),
        )
        events.append(disposal.event)
        if disposal.exit_code != 0 and exit_code == 0:
            exit_code = disposal.exit_code
        exit_code = final_exit_code(exit_code, events)
        _write_result(options, commands, events, exit_code)
    return exit_code


def run_meta_failures(options: Options) -> int:
    """Exercise provision, every child, and disposal failure deterministically."""
    if options.failure_fixture is None:
        raise RuntimeError("failure_fixture_required")
    fixture = cast(
        "dict[str, object]",
        json.loads(options.failure_fixture.read_text(encoding="utf-8")),
    )
    if (
        set(fixture) != {"schema", "failure_points"}
        or fixture["schema"] != "release-gate.fail-each-child.v1"
        or fixture["failure_points"] != list(FAILURE_POINTS)
    ):
        raise RuntimeError("failure_fixture_invalid")
    scenarios: list[dict[str, object]] = []
    for point in FAILURE_POINTS:
        calls: list[str] = []
        scenario = Options(
            attempt_dir=options.attempt_dir / point,
            admin_env=options.admin_env,
            database_env=options.database_env,
            base_sha=options.base_sha,
            reviewed_sha=options.reviewed_sha,
            failure_fixture=None,
            expect_meta_failure=False,
            wrapper=options.wrapper,
        )
        code = run(scenario, _injected_executor(point, calls))
        if code != INJECTED_EXIT_CODE or calls[-1] != "dispose":
            raise RuntimeError("failure_injection_not_fail_closed")
        scenarios.append(
            {
                "failure_point": point,
                "exit_code": code,
                "dispose_invoked": True,
                "commands_after_failure": 0,
            }
        )
    body: dict[str, object] = {
        "schema": "release-gate.local-qa-meta-failure.v1",
        "accepted": True,
        "wrapper": options.wrapper,
        "scenarios": scenarios,
    }
    write_json(options.attempt_dir / "result.json", body)
    return 0


def _write_result(
    options: Options,
    commands: tuple[Argv, ...],
    events: list[dict[str, object]],
    code: int,
) -> None:
    command_document = [list(argv) for argv in commands]
    canonical = json.dumps(
        command_document, sort_keys=True, separators=(",", ":")
    ).encode()
    body: dict[str, object] = {
        "schema": "release-gate.local-qa-result.v1",
        "accepted": code == 0,
        "wrapper": options.wrapper,
        "base_sha": options.base_sha,
        "reviewed_sha": options.reviewed_sha,
        "database_target": "monitor_migration_qa",
        "database_url_env": options.database_env,
        "command_manifest_sha256": hashlib.sha256(canonical).hexdigest(),
        "commands": command_document,
        "events": events,
        "exit_code": code,
    }
    write_json(options.attempt_dir / "result.json", body)


def _validate_options(options: Options) -> None:
    valid_shas = all(
        re.fullmatch(r"[0-9a-f]{40}", value) is not None
        for value in (options.base_sha, options.reviewed_sha)
    )
    if not valid_shas or options.wrapper not in {"powershell", "git-bash", "ci"} or not all((options.admin_env, options.database_env)):  # noqa: E501
        raise RuntimeError("orchestrator_arguments_invalid")


def _parse(argv: list[str]) -> Options:
    parser = argparse.ArgumentParser()
    _ = parser.add_argument("--attempt-dir", required=True)
    _ = parser.add_argument("--database-admin-url-env", required=True)
    _ = parser.add_argument("--database-url-env", required=True)
    _ = parser.add_argument("--base-sha", required=True)
    _ = parser.add_argument("--reviewed-sha", required=True)
    _ = parser.add_argument("--failure-fixture")
    _ = parser.add_argument("--expect-meta-failure", action="store_true")
    _ = parser.add_argument(
        "--wrapper",
        choices=("powershell", "git-bash", "ci"),
        required=True,
    )
    args = parser.parse_args(argv)
    return Options(
        attempt_dir=Path(args.attempt_dir),
        admin_env=args.database_admin_url_env,
        database_env=args.database_url_env,
        base_sha=args.base_sha,
        reviewed_sha=args.reviewed_sha,
        failure_fixture=Path(args.failure_fixture) if args.failure_fixture else None,
        expect_meta_failure=args.expect_meta_failure,
        wrapper=args.wrapper,
    )


def main(argv: list[str] | None = None) -> int:
    """Run the ordinary or deterministic meta-failure surface."""
    options = _parse(sys.argv[1:] if argv is None else argv)
    return run_meta_failures(options) if options.expect_meta_failure else run(options)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        _ = sys.stderr.write(f"local QA HOLD: {error}\n")
        raise SystemExit(2) from None
