from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import JsonValue, TypeAdapter
from scripts.source_bindings import (
    ADVISORY_LOCK_SQL,
    INTENT_COLUMNS,
    MANIFOLD_SOURCE_ID,
    BindingConflictError,
    BindingIntent,
    BindingPayload,
    BindingStateMachine,
    GitHubCommand,
    IntentJournal,
)

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "apps" / "api" / "scripts" / "source_bindings.py"


class RecordingGitHub:
    def __init__(self) -> None:
        self.calls: list[GitHubCommand] = []
        self.variables: dict[str, str] = {}

    def execute(self, command: GitHubCommand) -> str:
        self.calls.append(command)
        if command.argv[:3] == ("gh", "variable", "set"):
            self.variables[command.argv[3]] = command.stdin or ""
            return ""
        if command.argv[:3] == ("gh", "variable", "get"):
            return self.variables.get(command.argv[3], "")
        return ""


def intent(nonce: str = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa") -> BindingIntent:
    return BindingIntent.create(
        activation_nonce=UUID(nonce),
        payload=BindingPayload(
            protected_json='{"sources":["dcinside","manifold"]}',
            source_ids="dcinside,manifold",
            scope_version="phase1-reviewed-v1+manifold-v1",
        ),
    )


def test_apply_writes_exact_target_and_public_commit_marker_last() -> None:
    # Given: one prepared durable intent and a recording gh boundary.
    github = RecordingGitHub()
    journal = IntentJournal()
    machine = BindingStateMachine(github=github, journal=journal)

    # When: the reviewed binding is applied.
    receipt = machine.apply_github(intent())

    # Then: stdin and argv are exact, and scope is the final public marker.
    assert [call.argv for call in github.calls] == [
        (
            "gh",
            "secret",
            "set",
            "MONITOR_SOURCE_BINDINGS_JSON",
            "--repo",
            "63amg0010-cpu/prediction-market-monitor",
            "--env",
            "production-collector",
            "--body",
            "-",
        ),
        (
            "gh",
            "variable",
            "set",
            "MONITOR_SOURCE_IDS",
            "--repo",
            "63amg0010-cpu/prediction-market-monitor",
            "--env",
            "production-collector",
            "--body",
            "-",
        ),
        (
            "gh",
            "variable",
            "set",
            "MONITOR_SCOPE_VERSION",
            "--repo",
            "63amg0010-cpu/prediction-market-monitor",
            "--env",
            "production-collector",
            "--body",
            "-",
        ),
    ]
    assert [call.stdin for call in github.calls] == [
        '{"sources":["dcinside","manifold"]}',
        "dcinside,manifold",
        "phase1-reviewed-v1+manifold-v1",
    ]
    assert receipt.state == "binding_committed"


def test_lost_receipt_rereads_marker_without_rewriting_binding() -> None:
    # Given: GitHub accepted every write but the first client lost its receipt.
    github = RecordingGitHub()
    journal = IntentJournal()
    machine = BindingStateMachine(github=github, journal=journal)
    binding_intent = intent()
    _ = machine.apply_github(binding_intent)
    github.calls.clear()

    # When: the same nonce and payload hash resumes.
    receipt = machine.apply_github(binding_intent)

    # Then: recovery is read-only and confirms the exact public marker.
    assert receipt.recovered_after_lost_receipt
    assert [call.argv[:4] for call in github.calls] == [
        ("gh", "variable", "get", "MONITOR_SOURCE_IDS"),
        ("gh", "variable", "get", "MONITOR_SCOPE_VERSION"),
    ]


def test_different_inflight_intent_is_rejected_while_lock_is_held() -> None:
    # Given: one serialized binding intent owns the advisory-lock boundary.
    journal = IntentJournal()
    first = intent()
    second = intent("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    # When/Then: a competing nonce cannot begin until the first is terminal.
    with (
        journal.serialized(first),
        pytest.raises(BindingConflictError),
        journal.serialized(second),
    ):
        pass
    assert ADVISORY_LOCK_SQL == (
        "SELECT pg_advisory_lock(hashtext('production-collector-binding'))"
    )
    assert UUID("0890756a-ca23-5697-ae4c-0de527361064") == MANIFOLD_SOURCE_ID
    assert INTENT_COLUMNS == (
        "id",
        "activation_nonce",
        "source_id",
        "attestation_id",
        "payload_sha256",
        "prestate_sha256",
        "scope_version",
        "created_at_db",
    )
    assert "state" not in INTENT_COLUMNS


def test_partial_write_without_scope_marker_replays_every_write() -> None:
    # Given: a prior client wrote only the protected secret and source IDs.
    github = RecordingGitHub()
    journal = IntentJournal()
    binding_intent = intent()
    with journal.serialized(binding_intent):
        assert journal.begin_write()
    github.variables["MONITOR_SOURCE_IDS"] = binding_intent.payload.source_ids
    machine = BindingStateMachine(github=github, journal=journal)

    # When: the same durable intent resumes without its public scope marker.
    receipt = machine.apply_github(binding_intent)

    # Then: every write is replayed in the original commit-marker order.
    writes = [call for call in github.calls if call.argv[2] == "set"]
    assert [call.argv[3] for call in writes] == [
        "MONITOR_SOURCE_BINDINGS_JSON",
        "MONITOR_SOURCE_IDS",
        "MONITOR_SCOPE_VERSION",
    ]
    assert receipt.state == "binding_committed"


def test_restore_writes_old_scope_last_and_never_claims_restored() -> None:
    # Given: a committed failed scope and exact DCInside-only prestate.
    github = RecordingGitHub()
    journal = IntentJournal()
    machine = BindingStateMachine(github=github, journal=journal)
    binding_intent = intent()
    _ = machine.apply_github(binding_intent)
    github.calls.clear()
    prestate = BindingPayload(
        protected_json='{"sources":["dcinside"]}',
        source_ids="dcinside",
        scope_version="phase1-reviewed-v1",
    )

    # When: the technical restore writer replays the protected prestate.
    receipt = machine.restore_github(binding_intent, prestate)

    # Then: old scope is the final marker and terminal ownership is preserved.
    assert github.calls[-1].argv[3] == "MONITOR_SCOPE_VERSION"
    assert receipt.state == "restore_writing"
    assert all(transition != "restored" for transition in journal.transitions)


def test_render_cli_derives_payload_from_prestate_binding_and_platforms(
    tmp_path: Path,
) -> None:
    # Given: a captured DCInside prestate and one reviewed Manifold binding.
    prestate = tmp_path / "prestate.json"
    binding = tmp_path / "manifold.json"
    output = tmp_path / "rendered.json"
    _ = prestate.write_text(
        json.dumps(
            {
                "activation_nonce": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "command": "capture-prestate",
                "platforms": ["dcinside"],
                "protected_json": json.dumps(
                    [{"source_id": "dcinside", "platform": "dcinside"}],
                    separators=(",", ":"),
                ),
                "scope_version": "phase1-reviewed-v1",
                "source_ids": "dcinside",
            }
        ),
        encoding="utf-8",
    )
    _ = binding.write_text(
        json.dumps({"source_id": "manifold", "platform": "manifold"}),
        encoding="utf-8",
    )

    # When: the real CLI renders the successor payload.
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            str(SCRIPT),
            "render",
            "--activation-nonce",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "--predecessor-receipt",
            str(prestate),
            "--binding-file",
            str(binding),
            "--platform",
            "dcinside",
            "--platform",
            "manifold",
            "--json-out",
            str(output),
        ),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: output is derived from both files, rather than a static receipt.
    assert completed.returncode == 0, completed.stderr
    receipt = TypeAdapter(dict[str, JsonValue]).validate_json(output.read_bytes())
    assert receipt["platforms"] == ["dcinside", "manifold"]
    assert receipt["source_ids"] == "dcinside,manifold"
    protected_json = receipt["protected_json"]
    assert isinstance(protected_json, str)
    protected = TypeAdapter(list[dict[str, JsonValue]]).validate_json(protected_json)
    assert protected[-1] == {
        "platform": "manifold",
        "source_id": "manifold",
    }
    assert receipt["predecessor_sha256"]


def test_render_cli_rejects_platform_drift(tmp_path: Path) -> None:
    # Given: a predecessor claiming DCInside and a Manifold successor binding.
    prestate = tmp_path / "prestate.json"
    binding = tmp_path / "manifold.json"
    output = tmp_path / "rendered.json"
    _ = prestate.write_text(
        json.dumps(
            {
                "activation_nonce": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "command": "capture-prestate",
                "platforms": ["dcinside"],
                "protected_json": '[{"source_id":"dcinside","platform":"dcinside"}]',
                "scope_version": "phase1-reviewed-v1",
                "source_ids": "dcinside",
            }
        ),
        encoding="utf-8",
    )
    _ = binding.write_text(
        '{"source_id":"manifold","platform":"manifold"}',
        encoding="utf-8",
    )

    # When: the declared platform set omits the rendered Manifold binding.
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            str(SCRIPT),
            "render",
            "--activation-nonce",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "--predecessor-receipt",
            str(prestate),
            "--binding-file",
            str(binding),
            "--platform",
            "dcinside",
            "--json-out",
            str(output),
        ),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the boundary fails closed and emits no receipt.
    assert completed.returncode == 2
    assert "platform set does not match rendered bindings" in completed.stderr
    assert not output.exists()


def test_validate_cli_rejects_changed_predecessor(tmp_path: Path) -> None:
    # Given: a rendered payload bound to the canonical hash of its predecessor.
    nonce = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    predecessor = {
        "activation_nonce": nonce,
        "command": "capture-prestate",
        "platforms": ["dcinside"],
        "protected_json": '[{"platform":"dcinside","source_id":"dcinside"}]',
        "scope_version": "phase1-reviewed-v1",
        "source_ids": "dcinside",
    }
    predecessor_path = tmp_path / "prestate.json"
    payload_path = tmp_path / "payload.json"
    output = tmp_path / "validated.json"
    canonical = json.dumps(
        predecessor,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    payload = BindingPayload(
        protected_json=(
            '[{"platform":"dcinside","source_id":"dcinside"},'
            '{"platform":"manifold","source_id":"manifold"}]'
        ),
        source_ids="dcinside,manifold",
        scope_version="phase1-reviewed-v1+manifold-v1",
    )
    _ = payload_path.write_text(
        json.dumps(
            {
                "activation_nonce": nonce,
                "payload_sha256": payload.sha256,
                "predecessor_sha256": hashlib.sha256(canonical).hexdigest(),
                "protected_json": payload.protected_json,
                "scope_version": payload.scope_version,
                "source_ids": payload.source_ids,
            }
        ),
        encoding="utf-8",
    )
    predecessor["scope_version"] = "tampered"
    _ = predecessor_path.write_text(json.dumps(predecessor), encoding="utf-8")

    # When: validate receives the changed predecessor.
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            str(SCRIPT),
            "validate",
            "--activation-nonce",
            nonce,
            "--payload-receipt",
            str(payload_path),
            "--predecessor-receipt",
            str(predecessor_path),
            "--platform",
            "dcinside",
            "--platform",
            "manifold",
            "--json-out",
            str(output),
        ),
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: predecessor drift fails before a validation receipt is written.
    assert completed.returncode == 2
    assert "predecessor hash does not match payload" in completed.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "command",
    ["apply-github", "handshake-github", "finalize-github", "restore-github"],
)
def test_mutating_cli_commands_require_database_url(
    command: str,
    tmp_path: Path,
) -> None:
    # Given: a mutating command with the migration database variable absent.
    env = os.environ.copy()
    _ = env.pop("MISSING_MIGRATION_DATABASE_URL", None)
    output = tmp_path / f"{command}.json"

    # When: the command is invoked through the real script boundary.
    completed = subprocess.run(  # noqa: S603
        (
            sys.executable,
            str(SCRIPT),
            command,
            "--activation-nonce",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "--database-url-env",
            "MISSING_MIGRATION_DATABASE_URL",
            "--json-out",
            str(output),
        ),
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: no GitHub or receipt operation can occur without PostgreSQL.
    assert completed.returncode == 2
    assert "MIGRATION_DATABASE_URL environment is required" in completed.stderr
    assert not output.exists()
