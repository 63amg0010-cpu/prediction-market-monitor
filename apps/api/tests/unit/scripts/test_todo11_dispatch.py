from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, final

import pytest
from scripts.release_dispatch_commands import (
    bootstrap_dispatch,
    dispatch_workflow,
    recover_operation_receipt,
    verify_receipt,
)
from scripts.release_dispatch_contracts import (
    ChildResult,
    HoldError,
    JsonObject,
    canonical_bytes,
    sha256_hex,
)
from scripts.release_dispatch_selector import RunIdentity, select_run

if TYPE_CHECKING:
    from collections.abc import Callable

FIXTURES = Path(__file__).parents[2] / "fixtures" / "release-gate"
REPOSITORY = "63amg0010-cpu/prediction-market-monitor"
SHA = "a" * 40
PLAN = "b" * 64
ROOT = "c" * 64
NONCE = "11111111-1111-4111-8111-111111111111"
DISPATCH = "22222222-2222-4222-8222-222222222222"
FLOOR = "2026-07-29T01:02:03Z"


@final
class Runner:
    def __init__(self, reply: Callable[[tuple[str, ...]], ChildResult]) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._reply: Callable[[tuple[str, ...]], ChildResult] = reply

    def run(self, argv: tuple[str, ...], stdin: bytes | None = None) -> ChildResult:
        assert stdin is None
        self.calls.append(argv)
        return self._reply(argv)


def _root(command: str = "deployment-prestate") -> JsonObject:
    return {
        "schema_version": 1,
        "command": command,
        "attempt": 1,
        "reviewed_sha": SHA,
        "approved_plan_sha256": PLAN,
        "approval_round_id": "d" * 64,
        "approval_launch_sha256s": ["e" * 64, "f" * 64],
        "activation_nonce": NONCE,
        "dispatch_nonce": None,
        "state_before": "20260726_0009",
        "state_after": "20260726_0009",
        "accepted": True,
        "terminal_for_attempt": True,
        "retry_permitted": False,
        "predecessor_receipt_sha256": None,
    }


def _no_spend() -> JsonObject:
    return {
        "schema_version": 1,
        "command": "no-spend-preflight",
        "reviewed_sha": SHA,
        "approved_plan_sha256": PLAN,
        "activation_nonce": NONCE,
        "predecessor_receipt_sha256": sha256_hex(
            canonical_bytes(_review_root())
        ),
        "billing_disabled": True,
        "projection_below_70_percent": True,
    }


def _review_root() -> JsonObject:
    return {
        "schema_version": 1,
        "command": "deployment-prestate",
        "reviewed_sha": SHA,
        "approved_plan_sha256": PLAN,
        "approval_round_id": "d" * 64,
        "approval_launch_sha256s": ["e" * 64, "f" * 64],
        "activation_nonce": NONCE,
        "public_provider_names": ["github", "supabase", "vercel"],
        "protected_identity_hashes": {
            "github_repository": "1" * 64,
            "supabase_project": "2" * 64,
            "vercel_api_project": "3" * 64,
            "vercel_web_project": "4" * 64,
        },
    }


def _reservation(base: str = "ci", attempt: int = 1) -> JsonObject:
    return {
        **_root("dispatch-reserve"),
        "attempt": attempt,
        "dispatch_nonce": DISPATCH,
        "workflow": "ci.yml",
        "display_title": f"{base}-{DISPATCH}-attempt-{attempt}",
        "database_timestamps": {
            "created_at_db": FLOOR,
            "reserved_at_db": FLOOR,
            "selection_floor_at": FLOOR,
            "claimed_at_db": None,
        },
        "operation_inputs": {},
        "predecessor_receipt_sha256": "9" * 64,
        "receipt_sha256": "8" * 64,
    }


def _run(run_id: int = 41, created_at: str = FLOOR) -> JsonObject:
    return {
        "id": run_id,
        "workflow_id": 7,
        "display_title": f"ci-{DISPATCH}-attempt-1",
        "head_sha": SHA,
        "event": "workflow_dispatch",
        "created_at": created_at,
        "status": "completed",
        "conclusion": "success",
    }


def _page(total: int, runs: list[JsonObject]) -> ChildResult:
    return ChildResult(0, json.dumps({"total_count": total, "workflow_runs": runs}), "")


def test_bootstrap_dispatch_uses_server_floor_and_exact_single_mutation() -> None:
    date = "Wed, 29 Jul 2026 01:02:03 GMT"
    runner = Runner(
        lambda argv: ChildResult(0, f"HTTP/2 200 OK\nDate: {date}\n\n{{}}", "")
    )
    receipt = bootstrap_dispatch(
        runner,
        repository=REPOSITORY,
        workflow="migrate.yml",
        display_title=f"migrate-upgrade-20260727_0010-{DISPATCH}-attempt-1",
        deployment_prestate=canonical_bytes(_review_root()),
        no_spend_receipt=canonical_bytes(_no_spend()),
        failed_attempt_receipt=None,
        attempt=1,
        expected_sha=SHA,
        expected_plan_sha256=PLAN,
        activation_nonce=NONCE,
        dispatch_nonce=DISPATCH,
    )
    assert runner.calls[0] == ("gh", "api", "-i", "/rate_limit")
    assert runner.calls[1][:8] == (
        "gh", "workflow", "run", "migrate.yml", "--repo", REPOSITORY, "--ref", "main"
    )
    assert runner.calls[1][8:14] == (
        "-f", "operation=upgrade", "-f", "revision=20260727_0010",
        "-f", "confirm=migrate-production",
    )
    assert len(runner.calls) == 2
    assert receipt["selection_floor_at"] == FLOOR
    assert "HTTP" not in json.dumps(receipt)


def test_bootstrap_dispatch_rejects_non_schema_no_spend_before_mutation() -> None:
    runner = Runner(
        lambda argv: ChildResult(0, "Date: Wed, 29 Jul 2026 01:02:03 GMT", "")
    )
    invalid = {**_no_spend(), "approval_round_id": "d" * 64}
    with pytest.raises(HoldError, match="no_spend_receipt_invalid"):
        _ = bootstrap_dispatch(
            runner,
            repository=REPOSITORY,
            workflow="migrate.yml",
            display_title=(
                f"migrate-upgrade-20260727_0010-{DISPATCH}-attempt-1"
            ),
            deployment_prestate=canonical_bytes(_review_root()),
            no_spend_receipt=canonical_bytes(invalid),
            failed_attempt_receipt=None,
            attempt=1,
            expected_sha=SHA,
            expected_plan_sha256=PLAN,
            activation_nonce=NONCE,
            dispatch_nonce=DISPATCH,
        )
    assert runner.calls == []


def test_bootstrap_attempt_branch_has_no_hidden_retry() -> None:
    runner = Runner(
        lambda argv: ChildResult(0, "Date: Wed, 29 Jul 2026 01:02:03 GMT", "")
    )
    with pytest.raises(HoldError, match="attempt_one_failed_receipt_forbidden"):
        _ = bootstrap_dispatch(
            runner, repository=REPOSITORY, workflow="migrate.yml", display_title="x",
            deployment_prestate=canonical_bytes(_review_root()),
            no_spend_receipt=canonical_bytes(_no_spend()),
            failed_attempt_receipt=canonical_bytes(_root()), attempt=1,
            expected_sha=SHA, expected_plan_sha256=PLAN,
            activation_nonce=NONCE, dispatch_nonce=DISPATCH,
        )
    assert runner.calls == []


def test_dispatch_workflow_uses_schema_closed_spec_and_rejects_bootstrap() -> None:
    spec = (FIXTURES / "production-workflows.json").read_bytes()
    runner = Runner(lambda argv: ChildResult(0, "", ""))
    receipt = dispatch_workflow(
        runner, repository=REPOSITORY, workflow_spec=spec, base="ci",
        reservation=_reservation(), attempt=1, expected_sha=SHA,
        expected_plan_sha256=PLAN, activation_nonce=NONCE,
        dispatch_nonce=DISPATCH,
    )
    reservation_sha = "8" * 64
    assert runner.calls == [(
        "gh", "workflow", "run", "ci.yml", "--repo", REPOSITORY, "--ref", "main",
        "-f", "attempt=1", "-f", f"expected_commit_sha={SHA}",
        "-f", f"expected_plan_sha256={PLAN}", "-f", f"activation_nonce={NONCE}",
        "-f", f"dispatch_nonce={DISPATCH}", "-f",
        f"reservation_sha256={reservation_sha}",
    )]
    argv_bytes = b"\0".join(x.encode() for x in runner.calls[0])
    assert receipt["argv_sha256"] == sha256_hex(argv_bytes)
    with pytest.raises(HoldError, match="bootstrap_target_forbidden"):
        _ = dispatch_workflow(
            runner, repository=REPOSITORY, workflow_spec=spec,
            base="migrate-0010-bootstrap", reservation=_reservation(),
            attempt=1, expected_sha=SHA, expected_plan_sha256=PLAN,
            activation_nonce=NONCE, dispatch_nonce=DISPATCH,
        )
    assert len(runner.calls) == 1


def test_selector_maps_rest_id_and_stabilizes_complete_snapshot() -> None:
    calls = 0

    def reply(argv: tuple[str, ...]) -> ChildResult:
        nonlocal calls
        calls += 1
        page = int(argv[-1].rsplit("=", 1)[1])
        return _page(1, [_run()] if page == 1 else [])

    runner = Runner(reply)
    sleeps: list[float] = []
    selected = select_run(
        runner, identity=RunIdentity(
            repository=REPOSITORY, workflow="ci.yml",
            display_title=f"ci-{DISPATCH}-attempt-1", head_sha=SHA,
            activation_nonce=NONCE, dispatch_nonce=DISPATCH, attempt=1,
            selection_floor_at="2026-07-29T01:02:03.999999Z", claimed_run_id=41,
        ), sleep=sleeps.append,
    )
    assert selected["databaseId"] == 41
    assert selected["created_at"] == FLOOR
    assert sleeps == [10]
    assert calls == 2
    assert "created=%3E%3D2026-07-29T01%3A02%3A03Z" in runner.calls[0][-1]


@pytest.mark.parametrize("total", [1001, 1002])
def test_selector_holds_on_overflow_before_continuation(total: int) -> None:
    runner = Runner(lambda argv: _page(total, []))
    with pytest.raises(HoldError, match="selection_window_overflow"):
        _ = select_run(runner, identity=RunIdentity(
            repository=REPOSITORY, workflow="ci.yml", display_title="x",
            head_sha=SHA, activation_nonce=NONCE, dispatch_nonce=DISPATCH,
            attempt=1, selection_floor_at=FLOOR, claimed_run_id=41,
        ), sleep=lambda _: None)
    assert len(runner.calls) == 1


def test_verify_and_recover_bind_exact_run_and_never_redispatch() -> None:
    reservation = _reservation()
    selection = {**_run(), "databaseId": 41, "activation_nonce": NONCE,
                 "dispatch_nonce": DISPATCH, "attempt": 1}
    operation = {
        **_root("ci"), "dispatch_nonce": DISPATCH, "run_id": 41,
        "reservation_receipt_sha256": "8" * 64,
        "predecessor_receipt_sha256": "8" * 64,
    }
    verified = verify_receipt(
        canonical_bytes(operation), selection=selection, reservation=reservation,
        expected_command="ci", attempt=1, expected_sha=SHA,
        expected_plan_sha256=PLAN, activation_nonce=NONCE,
        dispatch_nonce=DISPATCH,
    )
    assert verified["artifact_sha256"] == sha256_hex(canonical_bytes(operation))
    runner = Runner(
        lambda argv: ChildResult(0, canonical_bytes(operation).decode(), "")
    )
    recovered = recover_operation_receipt(
        runner, repository=REPOSITORY, artifact_name=f"ci-{DISPATCH}-attempt-1",
        selection=selection,
    )
    assert recovered == canonical_bytes(operation)
    assert runner.calls[0][:3] == ("gh", "run", "download")
    assert all("workflow" not in call[:3] for call in runner.calls)
