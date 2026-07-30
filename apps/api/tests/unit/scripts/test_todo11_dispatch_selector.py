from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, final

import pytest
from scripts.release_dispatch_contracts import ChildResult, HoldError, JsonObject
from scripts.release_dispatch_selector import RunIdentity, select_run

if TYPE_CHECKING:
    from collections.abc import Callable

REPOSITORY = "63amg0010-cpu/prediction-market-monitor"
SHA = "a" * 40
NONCE = "11111111-1111-4111-8111-111111111111"
DISPATCH = "22222222-2222-4222-8222-222222222222"
FLOOR = "2026-07-29T01:02:03Z"
FIXTURES = Path(__file__).parents[2] / "fixtures" / "release-gate"


@final
class Runner:
    def __init__(self, reply: Callable[[tuple[str, ...]], ChildResult]) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.reply: Callable[[tuple[str, ...]], ChildResult] = reply

    def run(self, argv: tuple[str, ...], stdin: bytes | None = None) -> ChildResult:
        assert stdin is None
        self.calls.append(argv)
        return self.reply(argv)


def _identity(claim: int | None = 41) -> RunIdentity:
    return RunIdentity(
        repository=REPOSITORY,
        workflow="ci.yml",
        display_title=f"ci-{DISPATCH}-attempt-1",
        head_sha=SHA,
        activation_nonce=NONCE,
        dispatch_nonce=DISPATCH,
        attempt=1,
        selection_floor_at=FLOOR,
        claimed_run_id=claim,
    )


def _run(run_id: int, *, exact: bool = False) -> JsonObject:
    return {
        "id": run_id,
        "workflow_id": 7,
        "display_title": (
            f"ci-{DISPATCH}-attempt-1" if exact else f"unrelated-{run_id}"
        ),
        "head_sha": SHA,
        "event": "workflow_dispatch",
        "created_at": FLOOR,
        "status": "completed",
        "conclusion": "success",
    }


def test_workflow_specs_keep_bootstrap_out_of_post_ledger_dispatch() -> None:
    bootstrap = (FIXTURES / "bootstrap-0010-workflow.json").read_text()
    production = (FIXTURES / "production-workflows.json").read_text()
    assert '"base":"migrate-0010-bootstrap"' in bootstrap
    assert '"revision":"20260727_0010"' in bootstrap
    assert "migrate-0010-bootstrap" not in production
    for workflow in (
        "activation-evidence.yml", "ci.yml", "collect.yml", "migrate.yml", "verify.yml"
    ):
        assert f'"workflow":"{workflow}"' in production


@pytest.mark.parametrize("total", [999, 1000])
def test_selector_accepts_complete_boundary_windows(total: int) -> None:
    runs = [_run(index, exact=index == 41) for index in range(1, total + 1)]

    def reply(argv: tuple[str, ...]) -> ChildResult:
        page = int(argv[-1].rsplit("=", 1)[1])
        chunk = runs[(page - 1) * 100 : page * 100]
        return ChildResult(
            0, json.dumps({"total_count": total, "workflow_runs": chunk}), ""
        )

    runner = Runner(reply)
    selected = select_run(runner, identity=_identity(), sleep=lambda _: None)
    assert selected["databaseId"] == 41
    assert selected["total_count"] == total
    assert len(runner.calls) == 20
    assert {int(call[-1].rsplit("=", 1)[1]) for call in runner.calls} == set(
        range(1, 11)
    )


def test_selector_polls_exactly_24_times_at_five_seconds() -> None:
    runner = Runner(
        lambda argv: ChildResult(
            0, json.dumps({"total_count": 0, "workflow_runs": []}), ""
        )
    )
    sleeps: list[float] = []
    with pytest.raises(HoldError, match="run_correlation_zero"):
        _ = select_run(runner, identity=_identity(), sleep=sleeps.append)
    assert len(runner.calls) == 24
    assert sleeps == [5] * 23


def test_selector_rejects_duplicate_and_orphan_claims() -> None:
    duplicate = json.dumps(
        {
            "total_count": 2,
            "workflow_runs": [_run(41, exact=True), _run(42, exact=True)],
        }
    )
    with pytest.raises(HoldError, match="run_correlation_multiple"):
        _ = select_run(
            Runner(lambda argv: ChildResult(0, duplicate, "")),
            identity=_identity(),
            sleep=lambda _: None,
        )
    one = json.dumps({"total_count": 1, "workflow_runs": [_run(42, exact=True)]})
    with pytest.raises(HoldError, match="run_claim_mismatch"):
        _ = select_run(
            Runner(lambda argv: ChildResult(0, one, "")),
            identity=_identity(),
            sleep=lambda _: None,
        )
