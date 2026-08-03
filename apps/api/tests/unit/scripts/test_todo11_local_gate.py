# pyright: reportAny=false, reportArgumentType=false, reportUnknownLambdaType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportPrivateUsage=false, reportPrivateLocalImportUsage=false
# pyright: reportUnusedCallResult=false
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import local_db_gate as db_gate
from scripts import local_db_verification as db_verification
from scripts import local_qa_orchestrator as orchestrator
from scripts.activation_evidence_models import (
    ActivationEvidenceReceipt,
    PublicActivationAttestation,
)
from scripts.activation_migration_evidence import load_evidence
from scripts.local_db_guard import LocalDatabaseHoldError
from scripts.local_qa_evidence import (
    EVIDENCE_DIRECTORY,
    PRODUCTION_CREDENTIAL_ENV_NAMES,
)
from scripts.local_qa_execution import (
    OUTPUT_CHARACTER_LIMIT,
    REPLACEMENT_CHARACTER_LIMIT,
    console_safe_text,
    final_exit_code,
    redact_output,
    runtime_working_directory,
)
from scripts.local_qa_manifest import (
    dispose_argv,
    ordered_commands,
    provision_argv,
    runtime_argv,
)
from scripts.migration_dispatch_models import ReviewRoot

if TYPE_CHECKING:
    from collections.abc import Coroutine
    from types import TracebackType

GUARD = (
    ROOT
    / "apps"
    / "api"
    / "tests"
    / "fixtures"
    / "release-gate"
    / "local-qa-db-guard.json"
)
FAILURES = GUARD.with_name("fail-each-child.json")
BASE_SHA = "a" * 40
REVIEWED_SHA = "b" * 40
TARGET_URL = "postgresql+asyncpg://qa:redaction-sentinel@127.0.0.1/monitor_migration_qa"
ADMIN_URL = "postgresql+asyncpg://qa:redaction-sentinel@127.0.0.1/postgres"


def _run_isolated[T](awaitable: Coroutine[object, object, T]) -> T:
    def execute() -> tuple[T, bool]:
        loop = asyncio.SelectorEventLoop()
        with asyncio.Runner(loop_factory=lambda: loop) as runner:
            result = runner.run(awaitable)
        return result, loop.is_closed()

    with ThreadPoolExecutor(max_workers=1) as executor:
        result, loop_closed = executor.submit(execute).result()
    assert loop_closed
    return result


def options(tmp_path: Path, *, wrapper: str = "powershell") -> orchestrator.Options:
    return orchestrator.Options(
        attempt_dir=tmp_path,
        admin_env="QA_ADMIN_URL",
        database_env="QA_URL",
        base_sha=BASE_SHA,
        reviewed_sha=REVIEWED_SHA,
        failure_fixture=None,
        expect_meta_failure=False,
        wrapper=wrapper,
    )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://qa@project.supabase.co/monitor_migration_qa",
        "postgresql+asyncpg://qa@production.example/monitor_migration_qa",
        "postgresql+asyncpg://qa@127.0.0.1/postgres",
    ],
)
def test_production_supabase_and_nonexact_targets_fail_before_engine_or_ddl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
) -> None:
    engine_calls = 0

    def forbidden_engine(_url: object, **_kwargs: object) -> object:
        nonlocal engine_calls
        engine_calls += 1
        pytest.fail("engine must not be constructed")

    monkeypatch.setenv("QA_URL", url)
    monkeypatch.setattr(db_gate, "create_async_engine", forbidden_engine)
    request = db_gate.LocalDbRequest(
        phase="verify",
        database_url_env="QA_URL",
        expected_database="monitor_migration_qa",
        json_out=tmp_path / "result.json",
        expected_head="20260727_0011",
        expected_current="20260727_0011",
        expected_index="ix_post_versions_search_text_trgm",
    )

    with pytest.raises(LocalDatabaseHoldError):
        _ = _run_isolated(db_gate.execute_local_db(request))

    assert engine_calls == 0
    assert not request.json_out.exists()


def test_foreign_admin_target_fails_before_engine_or_ddl(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QA_URL", TARGET_URL)
    monkeypatch.setenv(
        "QA_ADMIN_URL",
        "postgresql+asyncpg://qa@project.supabase.co/postgres",
    )
    monkeypatch.setattr(
        db_gate,
        "create_async_engine",
        lambda *_args, **_kwargs: pytest.fail("engine constructed"),
    )
    request = db_gate.LocalDbRequest(
        phase="reprovision",
        database_url_env="QA_URL",
        admin_database_url_env="QA_ADMIN_URL",
        expected_database="monitor_migration_qa",
        required_start="20260726_0009",
        guard_file=GUARD,
        json_out=tmp_path / "result.json",
    )

    with pytest.raises(LocalDatabaseHoldError, match="admin_database_not_local"):
        _ = _run_isolated(db_gate.execute_local_db(request))


def test_isolated_runner_preserves_thread_current_loop_identity() -> None:
    failures: list[BaseException] = []

    async def current_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    def exercise() -> None:
        sentinel = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(sentinel)
        try:
            observed = _run_isolated(current_loop())
            assert observed is not sentinel
            assert observed.is_closed()
            assert asyncio.get_event_loop() is sentinel
        except BaseException as error:  # noqa: BLE001 - relay thread assertion.
            failures.append(error)
        finally:
            asyncio.set_event_loop(None)
            sentinel.close()

    worker = threading.Thread(target=exercise, daemon=True)
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    if failures:
        raise failures[0]


def test_registration_hook_exposes_every_local_database_phase() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    registered = db_gate.register_local_db_parser(subparsers)

    assert registered.prog.endswith("local-db")
    args = parser.parse_args(
        [
            "local-db",
            "--phase",
            "dispose",
            "--database-url-env",
            "QA_URL",
            "--expected-database",
            "monitor_migration_qa",
            "--json-out",
            "disposed.json",
        ]
    )
    assert db_gate.request_from_namespace(args).phase == "dispose"


def _correct_index_semantics() -> dict[str, object]:
    semantics: dict[str, object] = dict.fromkeys(
        db_verification.INDEX_SEMANTIC_FIELDS,
        True,
    )
    semantics["indexdef"] = (
        "CREATE INDEX ix_post_versions_search_text_trgm "
        "ON public.post_versions USING gin (search_text gin_trgm_ops)"
    )
    return semantics


def test_pg_normalized_indexdef_is_accepted_from_exact_catalog_semantics() -> None:
    semantics = _correct_index_semantics()

    assert 'COLLATE "C"' not in str(semantics["indexdef"])
    assert db_verification._index_semantics_valid(semantics)
    assert "pg_indexes" not in db_verification.INDEX_SEMANTICS_SQL
    assert "pg_collation" in db_verification.INDEX_SEMANTICS_SQL


@pytest.mark.parametrize(
    "drifted_field",
    [
        "unique_named_index",
        "exact_target",
        "exact_column",
        "exact_column_collation",
        "exact_index_collation",
        "exact_opclass",
        "exact_access_method",
        "valid_ready",
        "nonpartial",
    ],
)
def test_wrong_index_catalog_semantics_hold(drifted_field: str) -> None:
    semantics = _correct_index_semantics()
    semantics[drifted_field] = False

    assert not db_verification._index_semantics_valid(semantics)


def test_manifest_is_the_exact_ordered_twenty_command_contract(tmp_path: Path) -> None:
    commands = ordered_commands(tmp_path, BASE_SHA, REVIEWED_SHA, "QA_URL")

    assert len(commands) == 20
    assert commands[0] == ("uv", "sync", "--frozen", "--all-packages")
    assert commands[1] == ("pnpm", "install", "--frozen-lockfile")
    for number, command in enumerate(commands[4:7], start=5):
        assert command[5:9] == (
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(tmp_path / f"pytest-command-{number:02d}"),
        )
    assert commands[7][-4:] == (
        "alembic",
        "-c",
        "apps/api/alembic.ini",
        "heads",
    )
    assert commands[8][6:12] == (
        "local-db",
        "--phase",
        "upgrade",
        "--database-url-env",
        "QA_URL",
        "--expected-database",
    )
    assert commands[9][6:12] == (
        "local-db",
        "--phase",
        "verify",
        "--database-url-env",
        "QA_URL",
        "--expected-database",
    )
    assert commands[10] == (
        "uv",
        "run",
        "--package",
        "monitor-api",
        "python",
        "-m",
        "app.openapi",
    )
    assert [command[-1] for command in commands[11:16]] == [
        "check:api",
        "test",
        "typecheck",
        "lint",
        "build",
    ]
    assert [command[6] for command in commands[16:]] == [
        "secret-static-scan",
        "plan-compliance",
        "scope-fidelity",
        "links",
    ]


@pytest.mark.parametrize(
    ("platform", "argv", "expected"),
    [
        ("nt", ("pnpm", "install"), ("pnpm.cmd", "install")),
        ("nt", ("pnpm-other", "install"), ("pnpm-other", "install")),
        ("nt", ("uv", "sync"), ("uv", "sync")),
        ("posix", ("pnpm", "install"), ("pnpm", "install")),
    ],
)
def test_runtime_resolver_is_windows_pnpm_only(
    platform: str,
    argv: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    assert runtime_argv(argv, platform=platform) == expected


def test_windows_executor_resolves_child_without_changing_canonical_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    invoked: list[tuple[str, ...]] = []

    def completed(
        argv: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        invoked.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    def windows_runtime(argv: tuple[str, ...], *, platform: str) -> tuple[str, ...]:
        assert platform in {"nt", "posix"}
        return runtime_argv(argv, platform="nt")

    monkeypatch.setattr(orchestrator.subprocess, "run", completed)
    monkeypatch.setattr(orchestrator, "runtime_argv", windows_runtime)
    command = ordered_commands(tmp_path, BASE_SHA, REVIEWED_SHA, "QA_URL")[1]

    assert orchestrator._real_executor("command-02", command, {}) == 0
    assert command == ("pnpm", "install", "--frozen-lockfile")
    assert invoked == [("pnpm.cmd", "install", "--frozen-lockfile")]


def test_only_exact_command_eleven_gets_api_working_directory(
    tmp_path: Path,
) -> None:
    commands = ordered_commands(tmp_path, BASE_SHA, REVIEWED_SHA, "QA_URL")
    root = ROOT.resolve()
    api = root / "apps" / "api"

    for number, argv in enumerate(commands, start=1):
        label = f"command-{number:02d}"
        expected = api if number == 11 else root
        assert runtime_working_directory(label, argv, ROOT) == expected
    assert (
        runtime_working_directory(
            "provision",
            provision_argv(tmp_path, "QA_ADMIN_URL", "QA_URL"),
            ROOT,
        )
        == root
    )
    assert (
        runtime_working_directory(
            "dispose",
            dispose_argv(tmp_path, "QA_ADMIN_URL", "QA_URL"),
            ROOT,
        )
        == root
    )


def test_command_eleven_wrong_argv_or_missing_api_path_fails_closed(
    tmp_path: Path,
) -> None:
    command = ordered_commands(tmp_path, BASE_SHA, REVIEWED_SHA, "QA_URL")[10]

    with pytest.raises(RuntimeError, match="openapi_command_contract_invalid"):
        _ = runtime_working_directory(
            "command-11",
            (*command[:-1], "foreign.module"),
            ROOT,
        )
    with pytest.raises(
        RuntimeError,
        match="openapi_working_directory_invalid",
    ):
        _ = runtime_working_directory("command-11", command, tmp_path)


def test_real_executor_projects_command_eleven_cwd_without_argv_rewrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    invoked: list[tuple[tuple[str, ...], Path]] = []

    def completed(
        argv: tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        invoked.append((argv, Path(str(kwargs["cwd"]))))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(orchestrator.subprocess, "run", completed)
    command = ordered_commands(tmp_path, BASE_SHA, REVIEWED_SHA, "QA_URL")[10]

    assert orchestrator._real_executor("command-11", command, {}) == 0
    assert invoked == [(command, ROOT / "apps" / "api")]


def test_executor_decodes_utf8_independent_of_host_codepage_and_redacts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured_options: dict[str, object] = {}
    sensitive_value = "비밀-redaction-sentinel"

    def completed(
        argv: tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured_options.update(kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            f"설치 완료 {sensitive_value}\n",
            "",
        )

    monkeypatch.setattr(orchestrator.subprocess, "run", completed)

    assert (
        orchestrator._real_executor(
            "command-02",
            ("pnpm", "install"),
            {"DATABASE_URL": sensitive_value},
        )
        == 0
    )
    output = capsys.readouterr().out
    assert captured_options["encoding"] == "utf-8"
    assert captured_options["errors"] == "replace"
    assert "설치 완료 [REDACTED]" in output
    assert sensitive_value not in output


def test_cp949_console_escapes_u2009_and_redacts_ascii_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_value = "ascii-redaction-sentinel"

    class Cp949Stream:
        encoding: str = "cp949"

        def __init__(self) -> None:
            self.values: list[str] = []

        def write(self, value: str) -> int:
            _ = value.encode(self.encoding, errors="strict")
            self.values.append(value)
            return len(value)

    output = Cp949Stream()
    errors = Cp949Stream()

    def completed(
        argv: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        text = f"\u2009설치 {sensitive_value} 완료\n"
        return subprocess.CompletedProcess(argv, 0, text, "")

    monkeypatch.setattr(orchestrator.subprocess, "run", completed)
    monkeypatch.setattr(orchestrator.sys, "stdout", output)
    monkeypatch.setattr(orchestrator.sys, "stderr", errors)

    assert (
        orchestrator._real_executor(
            "command-02",
            ("pnpm", "install"),
            {"DATABASE_URL": sensitive_value},
        )
        == 0
    )
    rendered = "".join(output.values)
    assert rendered.startswith("\\u2009설치 ")
    assert "[REDACTED]" in rendered
    assert sensitive_value not in rendered


def test_invalid_utf8_bytes_cannot_hide_neighboring_ascii_secret() -> None:
    sensitive_value = "ascii-redaction-sentinel"
    raw = b"\xff" + sensitive_value.encode() + b"\xfe"
    decoded = raw.decode("utf-8", errors="replace")

    rendered = redact_output(decoded, {"DATABASE_URL": sensitive_value})

    assert rendered == "\ufffd[REDACTED]\ufffd"
    assert sensitive_value not in rendered


def test_alembic_head_output_keeps_strict_utf8_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_options: dict[str, object] = {}

    def completed(
        argv: tuple[str, ...],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        captured_options.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, "20260727_0011 (head)\n", "")

    monkeypatch.setattr(orchestrator.subprocess, "run", completed)

    assert orchestrator._real_executor("command-08", ("uv", "run"), {}) == 0
    assert captured_options["encoding"] == "utf-8"
    assert captured_options["errors"] == "strict"


def test_output_redaction_precedes_cap_and_bounds_replacement_markers() -> None:
    sensitive_value = "ascii-redaction-sentinel"
    raw = (
        "\ufffd" * REPLACEMENT_CHARACTER_LIMIT
        + sensitive_value
        + "x" * OUTPUT_CHARACTER_LIMIT
    )

    rendered = redact_output(raw, {"DATABASE_URL": sensitive_value})

    assert len(rendered) == OUTPUT_CHARACTER_LIMIT
    assert rendered.endswith("[output truncated]\n")
    assert sensitive_value not in rendered
    assert console_safe_text("\u2009한글", "cp949") == "\\u2009한글"


def test_excessive_replacement_markers_fail_closed() -> None:
    raw = "\ufffd" * (REPLACEMENT_CHARACTER_LIMIT + 1)

    with pytest.raises(
        RuntimeError,
        match="child_output_replacement_limit_exceeded",
    ):
        _ = redact_output(raw, {})


def test_console_write_failure_is_failed_event_and_still_disposes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QA_URL", TARGET_URL)
    subprocess_calls = 0

    class BrokenConsole:
        encoding: str = "cp949"

        def write(self, _value: str) -> int:
            message = "console-write-failed"
            raise OSError(message)

    def completed(
        argv: tuple[str, ...],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal subprocess_calls
        subprocess_calls += 1
        return subprocess.CompletedProcess(argv, 0, "\u2009진단\n", "")

    monkeypatch.setattr(orchestrator.subprocess, "run", completed)
    monkeypatch.setattr(orchestrator.sys, "stdout", BrokenConsole())

    assert orchestrator.run(options(tmp_path)) == 2

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert subprocess_calls == 2
    assert [event["label"] for event in result["events"]] == [
        "provision",
        "dispose",
    ]
    assert all(
        event["failure_code"] == "child_executor_exception"
        for event in result["events"]
    )
    assert result["accepted"] is False
    assert not (tmp_path / EVIDENCE_DIRECTORY).exists()


def test_runtime_resolver_rejects_unknown_platform() -> None:
    with pytest.raises(RuntimeError, match="unsupported_execution_platform"):
        _ = runtime_argv(("pnpm", "install"), platform="unknown")


def test_both_os_wrappers_delegate_only_to_the_same_manifest_owner() -> None:
    powershell = (ROOT / "scripts" / "verify-fresh-search.ps1").read_text()
    git_bash = (ROOT / "scripts" / "verify-fresh-search.sh").read_text()

    for source in (powershell, git_bash):
        assert "apps/api/scripts/local_qa_orchestrator.py" in source
        assert "fresh_search_release_gate.py" not in source
        assert "alembic" not in source
        assert "DATABASE_URL" not in source
        assert "uv run --no-sync --package monitor-api python" in source


def test_provision_and_dispose_argv_are_guarded_and_redacted(tmp_path: Path) -> None:
    provision = provision_argv(tmp_path, "QA_ADMIN_URL", "QA_URL")
    dispose = dispose_argv(tmp_path, "QA_ADMIN_URL", "QA_URL")
    rendered = json.dumps([provision, dispose])

    assert "--required-start" in provision
    assert "20260726_0009" in provision
    assert "--guard-file" in provision
    assert "--guard-file" in dispose
    assert "redaction-sentinel" not in rendered
    assert TARGET_URL not in rendered


def test_command_nine_failure_skips_ten_and_disposes_without_secret(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QA_URL", TARGET_URL)
    calls: list[str] = []
    executor = orchestrator._injected_executor("command-09", calls)

    code = orchestrator.run(options(tmp_path), executor)

    assert code == orchestrator.INJECTED_EXIT_CODE
    assert calls[-2:] == ["command-09", "dispose"]
    assert "command-10" not in calls
    result_text = (tmp_path / "result.json").read_text(encoding="utf-8")
    assert TARGET_URL not in result_text
    assert "redaction-sentinel" not in result_text
    assert not (tmp_path / EVIDENCE_DIRECTORY).exists()


def test_database_environment_is_scoped_only_to_lifecycle_children(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    admin_value = ADMIN_URL
    monkeypatch.setenv("QA_URL", TARGET_URL)
    monkeypatch.setenv("QA_ADMIN_URL", admin_value)
    monkeypatch.setenv("DATABASE_URL", "caller-database-redaction-sentinel")
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "caller-migration-redaction-sentinel",
    )
    observed: dict[str, dict[str, str]] = {}

    def observing_executor(
        label: str,
        _argv: tuple[str, ...],
        env: dict[str, str],
    ) -> int:
        observed[label] = env.copy()
        return 0

    assert orchestrator.run(options(tmp_path), observing_executor) == 0

    lifecycle = {"provision", "command-09", "command-10", "dispose"}
    for label, environment in observed.items():
        assert ("QA_URL" in environment) is (label in lifecycle)
        if label in lifecycle:
            assert environment["QA_URL"] == TARGET_URL
        assert "DATABASE_URL" not in environment
        assert "MIGRATION_DATABASE_URL" not in environment
        if label in {"provision", "dispose"}:
            assert environment["QA_ADMIN_URL"] == admin_value
        else:
            assert "QA_ADMIN_URL" not in environment
        if label not in lifecycle:
            assert TARGET_URL not in environment.values()
            assert admin_value not in environment.values()
    assert "MIGRATION_ACTIVATION_ATTESTATION_PATH" in observed["command-09"]
    assert "MIGRATION_ACTIVATION_EVIDENCE_RECEIPT_PATH" in observed["command-09"]
    assert not (tmp_path / EVIDENCE_DIRECTORY).exists()


def test_unit_contract_child_cannot_open_inherited_qa_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QA_URL", TARGET_URL)
    monkeypatch.setenv("QA_ADMIN_URL", ADMIN_URL)
    connection_attempts = 0

    def isolated_executor(
        label: str,
        _argv: tuple[str, ...],
        env: dict[str, str],
    ) -> int:
        nonlocal connection_attempts
        if label == "command-05" and any(
            name in env for name in ("DATABASE_URL", "MIGRATION_DATABASE_URL", "QA_URL")
        ):
            connection_attempts += 1
        return 0

    assert orchestrator.run(options(tmp_path), isolated_executor) == 0
    assert connection_attempts == 0
    assert not (tmp_path / EVIDENCE_DIRECTORY).exists()


def test_command_nine_gets_valid_fresh_0011_evidence_only_for_its_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QA_URL", TARGET_URL)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "production-db-redaction-sentinel")
    monkeypatch.setenv("MANIFOLD_TOKEN", "production-token-redaction-sentinel")
    observed_attestations: list[PublicActivationAttestation] = []
    observed_receipts: list[ActivationEvidenceReceipt] = []
    observed_shas: list[str] = []

    def validating_executor(
        label: str,
        _argv: tuple[str, ...],
        env: dict[str, str],
    ) -> int:
        assert all(name not in env for name in PRODUCTION_CREDENTIAL_ENV_NAMES)
        attestation_env = "MIGRATION_ACTIVATION_ATTESTATION_PATH"
        receipt_env = "MIGRATION_ACTIVATION_EVIDENCE_RECEIPT_PATH"
        if label == "command-09":
            assert attestation_env in env
            assert receipt_env in env
            correction_root = ReviewRoot.model_validate_json(
                base64.b64decode(
                    env["MIGRATION_CORRECTION_REVIEW_ROOT_B64"],
                    validate=True,
                )
            )
            rebind_root = ReviewRoot.model_validate_json(
                base64.b64decode(
                    env["MIGRATION_REBIND_REVIEW_ROOT_B64"],
                    validate=True,
                )
            )
            assert rebind_root.reviewed_sha == correction_root.reviewed_sha
            assert (
                rebind_root.approved_plan_sha256 == correction_root.approved_plan_sha256
            )
            assert (
                rebind_root.protected_identity_hashes
                == correction_root.protected_identity_hashes
            )
            assert rebind_root.activation_nonce != correction_root.activation_nonce
            assert rebind_root.approval_round_id != correction_root.approval_round_id
            with monkeypatch.context() as context:
                context.setenv(attestation_env, env[attestation_env])
                context.setenv(receipt_env, env[receipt_env])
                attestation, _, attestation_sha, _ = load_evidence("phase1-reviewed-v1")
            receipt = ActivationEvidenceReceipt.model_validate_json(
                Path(env[receipt_env]).read_bytes()
            )
            observed_attestations.append(attestation)
            observed_receipts.append(receipt)
            observed_shas.append(attestation_sha)
        elif label != "dispose":
            assert attestation_env not in env
            assert receipt_env not in env
        return 0

    assert orchestrator.run(options(tmp_path), validating_executor) == 0

    attestation = observed_attestations[0]
    receipt = observed_receipts[0]
    assert attestation.reviewed_sha == REVIEWED_SHA
    assert receipt.head_sha == REVIEWED_SHA
    assert receipt.attestation_sha256 == observed_shas[0]
    assert receipt.database_time == attestation.evidence_database_time
    assert (datetime.now(UTC) - receipt.database_time).total_seconds() < 10
    assert not (tmp_path / EVIDENCE_DIRECTORY).exists()
    result = (tmp_path / "result.json").read_text(encoding="utf-8")
    assert "activation-attestation-generation-1.json" not in result
    assert "activation-evidence-receipt.json" not in result


def test_executor_type_error_is_stable_failed_event_and_disposes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QA_URL", TARGET_URL)
    calls: list[str] = []

    def raising_executor(
        label: str,
        _argv: tuple[str, ...],
        _env: dict[str, str],
    ) -> int:
        calls.append(label)
        if label == "command-03":
            message = "sensitive-executor-detail"
            raise TypeError(message)
        return 0

    assert orchestrator.run(options(tmp_path), raising_executor) == 2

    assert calls[-1] == "dispose"
    result_text = (tmp_path / "result.json").read_text(encoding="utf-8")
    result = json.loads(result_text)
    assert result["accepted"] is False
    assert result["exit_code"] == 2
    assert result["events"][-2] == {
        "accepted": False,
        "exit_code": 2,
        "failure_code": "child_executor_exception",
        "label": "command-03",
    }
    assert result["events"][-1]["label"] == "dispose"
    assert "sensitive-executor-detail" not in result_text


def test_partial_successful_event_set_can_never_be_accepted() -> None:
    events: list[dict[str, object]] = [
        {"label": "provision", "exit_code": 0, "accepted": True},
        {"label": "dispose", "exit_code": 0, "accepted": True},
    ]

    assert final_exit_code(0, events) == 2


def test_failure_fixture_injects_provision_every_child_and_dispose(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("QA_URL", TARGET_URL)
    ordinary = options(tmp_path)
    meta = orchestrator.Options(
        attempt_dir=ordinary.attempt_dir,
        admin_env=ordinary.admin_env,
        database_env=ordinary.database_env,
        base_sha=ordinary.base_sha,
        reviewed_sha=ordinary.reviewed_sha,
        failure_fixture=FAILURES,
        expect_meta_failure=True,
        wrapper=ordinary.wrapper,
    )

    assert orchestrator.run_meta_failures(meta) == 0

    result = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert [item["failure_point"] for item in result["scenarios"]] == list(
        orchestrator.FAILURE_POINTS
    )
    assert all(item["dispose_invoked"] for item in result["scenarios"])
    assert TARGET_URL not in json.dumps(result)


def test_reprovision_holds_lock_across_drop_create_upgrade_and_verify(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []

    class StubConnection:
        async def execute(
            self,
            statement: object,
            _parameters: object = None,
        ) -> None:
            events.append(str(statement).strip().splitlines()[0])

    class StubContext:
        async def __aenter__(self) -> StubConnection:
            events.append("connect")
            return StubConnection()

        async def __aexit__(
            self,
            _type: type[BaseException] | None,
            _error: BaseException | None,
            _traceback: TracebackType | None,
        ) -> None:
            events.append("disconnect")

    class StubEngine:
        def connect(self) -> StubContext:
            return StubContext()

        async def dispose(self) -> None:
            events.append("engine_dispose")

    def upgrade(_url: str, revision: str) -> None:
        events.append(f"upgrade:{revision}")

    async def baseline(_url: str, revision: str) -> dict[str, object]:
        events.append(f"verify:{revision}")
        return {"current_revision": revision}

    monkeypatch.setattr(
        db_gate,
        "create_async_engine",
        lambda *_args, **_kw: StubEngine(),
    )
    monkeypatch.setattr(db_gate, "_alembic_upgrade", upgrade)
    monkeypatch.setattr(db_gate, "baseline_checks", baseline)
    monkeypatch.setenv("QA_ADMIN_URL", ADMIN_URL)
    request = db_gate.LocalDbRequest(
        phase="reprovision",
        database_url_env="QA_URL",
        admin_database_url_env="QA_ADMIN_URL",
        expected_database="monitor_migration_qa",
        required_start="20260726_0009",
        guard_file=GUARD,
        json_out=tmp_path / "result.json",
    )

    checks = _run_isolated(
        db_gate._maintenance_phase(
            request,
            db_gate.guarded_target(TARGET_URL, "monitor_migration_qa"),
            TARGET_URL,
        )
    )

    assert checks["database_recreated"] is True
    assert events == [
        "connect",
        "SELECT pg_advisory_lock(hashtext(:key))",
        "DO $$",
        'DROP DATABASE IF EXISTS "monitor_migration_qa" WITH (FORCE)',
        'CREATE DATABASE "monitor_migration_qa"',
        "upgrade:20260726_0009",
        "verify:20260726_0009",
        "SELECT pg_advisory_unlock(hashtext(:key))",
        "disconnect",
        "engine_dispose",
    ]
