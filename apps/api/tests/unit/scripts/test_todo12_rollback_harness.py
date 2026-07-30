from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from scripts import release_rollback_harness as harness
from scripts.release_rollback_harness_db import MANIFOLD_SQL

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

SHA = "a" * 40
URL = "postgresql+asyncpg://qa:secret@127.0.0.1/monitor_migration_qa"


class FakeDatabase:
    def __init__(self, states: Sequence[harness.DatabaseSnapshot]) -> None:
        self.states: list[harness.DatabaseSnapshot] = list(states)
        self.migrations: list[tuple[str, str]] = []

    def snapshot(self, url: str) -> harness.DatabaseSnapshot:
        _ = url
        if not self.states:
            pytest.fail("unexpected snapshot")
        return self.states.pop(0)

    def migrate(self, url: str, direction: str, revision: str) -> None:
        _ = url
        self.migrations.append((direction, revision))


def state(
    revision: str,
    *,
    manifold_present: bool,
    manifold_enabled: bool = False,
    pointers_null: bool = True,
    dcinside_hash: str = "d" * 64,
) -> harness.DatabaseSnapshot:
    return harness.DatabaseSnapshot(
        revision=revision,
        manifold_present=manifold_present,
        manifold_enabled=manifold_enabled,
        manifold_pointers_null=pointers_null,
        dcinside_binding_sha256=dcinside_hash,
    )


def options(
    tmp_path: Path,
    *,
    mode: str = "disposable",
    database_url_env: str = "MIGRATION_QA_DATABASE_URL",
    stub_external: bool = True,
    expected_sha: str = SHA,
) -> harness.Options:
    return harness.Options(
        mode=mode,
        database_url_env=database_url_env,
        stub_external=stub_external,
        expected_sha=expected_sha,
        json_out=tmp_path / "rollback-harness.json",
    )


def successful_database() -> FakeDatabase:
    return FakeDatabase(
        [
            state("20260727_0010", manifold_present=False),
            state("20260727_0011", manifold_present=True),
            state("20260727_0010", manifold_present=True),
        ]
    )


def test_snapshot_query_is_compatible_with_db0010_missing_pointer_columns() -> None:
    assert "to_jsonb(source)->>'current_budget_id'" in MANIFOLD_SQL
    assert "to_jsonb(source)->>'current_binding_id'" in MANIFOLD_SQL
    assert "to_jsonb(source)->>'current_cadence_id'" in MANIFOLD_SQL
    assert "current_budget_id IS NULL" not in MANIFOLD_SQL


def test_exact_disposable_sequence_and_schema_closed_redacted_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MIGRATION_QA_DATABASE_URL", URL)
    database = successful_database()

    receipt = harness.run_harness(options(tmp_path), database=database)

    assert database.migrations == [
        ("upgrade", "20260727_0011"),
        ("downgrade", "20260727_0010"),
    ]
    assert set(receipt) == {
        "accepted",
        "database",
        "external",
        "mode",
        "reviewed_sha",
        "schema",
    }
    assert set(receipt["database"]) == {
        "dcinside_binding_sha256",
        "dcinside_preserved",
        "manifold_enabled",
        "manifold_pointers_null",
        "name",
        "revision_after",
        "revision_before",
        "revision_peak",
    }
    assert receipt["database"]["revision_after"] == "20260727_0010"
    assert receipt["database"]["manifold_enabled"] is False
    assert receipt["database"]["dcinside_preserved"] is True
    assert receipt["external"]["executed_count"] == 0
    encoded = json.dumps(receipt, sort_keys=True)
    assert "secret" not in encoded
    assert "postgresql" not in encoded
    assert "production_access" in encoded
    assert receipt == json.loads(options(tmp_path).json_out.read_text("utf-8"))
    assert not (tmp_path / ".local-qa-activation-evidence").exists()


def test_matrix_b_stub_argv_is_exact_and_has_no_secret_or_network_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MIGRATION_QA_DATABASE_URL", URL)

    receipt = harness.run_harness(options(tmp_path), database=successful_database())

    external = receipt["external"]
    assert set(external) == {
        "commands",
        "executed_count",
        "mode",
        "network_access",
        "production_access",
    }
    assert external["mode"] == "stub-only"
    assert external["network_access"] is False
    assert external["production_access"] is False
    assert external["executed_count"] == 0
    commands = external["commands"]
    assert [command["stage"] for command in commands] == [
        "downgrade-workflow",
        "binding-restore",
        "binding-restore-verify",
        "api-target-sha",
        "api-protected-sha",
        "api-reachable",
        "api-worktree-add",
        "api-pull",
        "api-build",
        "api-deploy",
        "api-inspect",
        "api-alias",
        "api-health",
        "api-worktree-remove",
        "web-target-sha",
        "web-protected-sha",
        "web-reachable",
        "web-worktree-add",
        "web-pull",
        "web-build",
        "web-deploy",
        "web-inspect",
        "web-alias",
        "web-health",
        "web-worktree-remove",
    ]
    assert all(set(command) == {"argv", "cwd", "stage"} for command in commands)
    by_stage = {command["stage"]: command["argv"] for command in commands}
    assert by_stage["downgrade-workflow"][:8] == [
        "gh",
        "workflow",
        "run",
        "migrate.yml",
        "--repo",
        "63amg0010-cpu/prediction-market-monitor",
        "--ref",
        "main",
    ]
    assert by_stage["api-target-sha"] == [
        "git",
        "rev-parse",
        "--verify",
        f"{SHA}^{{commit}}",
    ]
    assert by_stage["api-pull"] == [
        "npx",
        "--yes",
        "vercel@51.7.0",
        "pull",
        "--environment=production",
        "--scope",
        "63amg0010-5358-projects",
        "--yes",
    ]
    assert by_stage["api-build"][3:5] == ["build", "--prod"]
    assert by_stage["api-deploy"][3:6] == ["deploy", "--prebuilt", "--prod"]
    assert by_stage["api-inspect"][-3:] == [
        "--scope",
        "63amg0010-5358-projects",
        "--json",
    ]
    flattened = json.dumps(commands, sort_keys=True)
    assert "secret" not in flattened
    assert "VERCEL_TOKEN" not in flattened
    assert URL not in flattened
    assert SHA in flattened


@pytest.mark.parametrize(
    "case",
    [
        ("production", "MIGRATION_QA_DATABASE_URL", True, SHA, "mode"),
        (
            "disposable",
            "MIGRATION_DATABASE_URL",
            True,
            SHA,
            "database_url_env",
        ),
        ("disposable", "MIGRATION_QA_DATABASE_URL", False, SHA, "stub_external"),
        ("disposable", "MIGRATION_QA_DATABASE_URL", True, "bad", "expected_sha"),
    ],
)
def test_nonexact_cli_contract_holds_before_database_or_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: tuple[str, str, bool, str, str],
) -> None:
    monkeypatch.setenv("MIGRATION_QA_DATABASE_URL", URL)
    database = successful_database()
    mode, database_env, stub_external, expected_sha, reason = case

    with pytest.raises(harness.RollbackHarnessHoldError, match=reason):
        _ = harness.run_harness(
            options(
                tmp_path,
                mode=mode,
                database_url_env=database_env,
                stub_external=stub_external,
                expected_sha=expected_sha,
            ),
            database=database,
        )

    assert database.migrations == []
    assert not options(tmp_path).json_out.exists()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://qa@db.example/monitor_migration_qa",
        "postgresql+asyncpg://qa@project.supabase.co/monitor_migration_qa",
        "postgresql+asyncpg://qa@127.0.0.1/postgres",
        "postgresql://qa@127.0.0.1/monitor_migration_qa",
    ],
)
def test_database_guard_rejects_nonloopback_nonexact_or_wrong_driver_before_use(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    url: str,
) -> None:
    monkeypatch.setenv("MIGRATION_QA_DATABASE_URL", url)
    database = successful_database()

    with pytest.raises(harness.RollbackHarnessHoldError):
        _ = harness.run_harness(options(tmp_path), database=database)

    assert database.migrations == []
    assert not options(tmp_path).json_out.exists()


@pytest.mark.parametrize(
    "states",
    [
        [state("20260726_0009", manifold_present=False)],
        [
            state("20260727_0010", manifold_present=False),
            state("20260727_0010", manifold_present=True),
            state("20260727_0010", manifold_present=True),
        ],
        [
            state("20260727_0010", manifold_present=False),
            state("20260727_0011", manifold_present=True),
            state("20260727_0010", manifold_present=True, manifold_enabled=True),
        ],
        [
            state("20260727_0010", manifold_present=False),
            state("20260727_0011", manifold_present=True),
            state("20260727_0010", manifold_present=True, pointers_null=False),
        ],
        [
            state("20260727_0010", manifold_present=False),
            state("20260727_0011", manifold_present=True),
            state(
                "20260727_0010",
                manifold_present=True,
                dcinside_hash="e" * 64,
            ),
        ],
    ],
)
def test_revision_or_poststate_mismatch_holds_without_accepted_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    states: list[harness.DatabaseSnapshot],
) -> None:
    monkeypatch.setenv("MIGRATION_QA_DATABASE_URL", URL)

    with pytest.raises(harness.RollbackHarnessHoldError):
        _ = harness.run_harness(options(tmp_path), database=FakeDatabase(states))

    assert not options(tmp_path).json_out.exists()


def test_parser_requires_the_exact_plan_argv(tmp_path: Path) -> None:
    parsed = harness.parse_args(
        [
            "--mode",
            "disposable",
            "--database-url-env",
            "MIGRATION_QA_DATABASE_URL",
            "--stub-external",
            "--expected-sha",
            SHA,
            "--json-out",
            str(tmp_path / "result.json"),
        ]
    )
    assert parsed == harness.Options(
        mode="disposable",
        database_url_env="MIGRATION_QA_DATABASE_URL",
        stub_external=True,
        expected_sha=SHA,
        json_out=tmp_path / "result.json",
    )

    with pytest.raises(SystemExit):
        _ = harness.parse_args(
            [
                "--mode",
                "disposable",
                "--database-url-env",
                "MIGRATION_QA_DATABASE_URL",
                "--expected-sha",
                SHA,
                "--json-out",
                str(tmp_path / "result.json"),
            ]
        )


def test_rerun_accepts_retained_but_inert_manifold_row(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MIGRATION_QA_DATABASE_URL", URL)
    database = FakeDatabase(
        [
            state("20260727_0010", manifold_present=True),
            state("20260727_0011", manifold_present=True),
            state("20260727_0010", manifold_present=True),
        ]
    )

    receipt = harness.run_harness(options(tmp_path), database=database)

    assert receipt["accepted"] is True
    assert database.migrations == [
        ("upgrade", "20260727_0011"),
        ("downgrade", "20260727_0010"),
    ]


def test_output_refuses_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MIGRATION_QA_DATABASE_URL", URL)
    target = options(tmp_path).json_out
    _ = target.write_text("owner-data", encoding="utf-8")

    with pytest.raises(harness.RollbackHarnessHoldError, match="json_out_exists"):
        _ = harness.run_harness(options(tmp_path), database=successful_database())

    assert target.read_text("utf-8") == "owner-data"
