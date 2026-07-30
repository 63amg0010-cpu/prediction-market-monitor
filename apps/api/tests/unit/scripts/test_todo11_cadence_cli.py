"""Exercise the phase-specific durable cadence CLI receipts."""

# pyright: reportAny=false, reportArgumentType=false, reportPrivateUsage=false
# pyright: reportUnannotatedClassAttribute=false

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import UUID

from app.services.release.receipts import canonicalize

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.release_cadence_models import CadenceEpoch, CadenceSlot  # noqa: E402
from scripts.release_gate_cli_cadence import _execute  # noqa: E402

if TYPE_CHECKING:
    from scripts.release_chain_common import JsonObject

SHA = "a" * 40
PLAN = "b" * 64
NONCE = "11111111-1111-4111-8111-111111111111"
EPOCH = "22222222-2222-4222-8222-222222222222"
SOURCE_A = "33333333-3333-4333-8333-333333333333"
SOURCE_B = "44444444-4444-4444-8444-444444444444"
NOW = datetime(2026, 7, 29, 3, 0, tzinfo=UTC)


def _receipt(command: str, details: JsonObject) -> JsonObject:
    body: JsonObject = {
        "schema": "release-chain-receipt.v1",
        "command": command,
        "reviewed_sha": SHA,
        "approved_plan_sha256": PLAN,
        "approval_round_id": "c" * 64,
        "approval_launch_sha256s": ["d" * 64, "e" * 64],
        "activation_nonce": NONCE,
        "dispatch_nonce": None,
        "attempt": 0,
        "database_timestamps": {"created_at_db": "2026-07-29T03:00:00Z"},
        "accepted": True,
        "terminal_for_attempt": True,
        "retry_permitted": False,
        "predecessor_receipt_sha256": None,
        "details": details,
    }
    return {**body, "receipt_sha256": sha256(canonicalize(body)).hexdigest()}


def _write(path: Path, value: JsonObject) -> None:
    _ = path.write_bytes(canonicalize(value))


class _Store:
    epoch: CadenceEpoch | None = None
    slots: tuple[CadenceSlot, ...] = ()

    async def materialize(
        self,
        epoch: CadenceEpoch,
        slots: tuple[CadenceSlot, ...],
    ) -> None:
        self.epoch, self.slots = epoch, slots


class _Runtime:
    def __init__(
        self,
        *,
        observed_at: datetime = NOW,
        accepted_collection: int = 0,
        accepted_verifier: int = 0,
    ) -> None:
        self.store = _Store()
        self.observed_at = observed_at
        self.accepted_collection = accepted_collection
        self.accepted_verifier = accepted_verifier

    async def snapshot(self, _epoch_id: UUID) -> object:
        epoch = self.store.epoch
        assert epoch is not None
        return SimpleNamespace(
            epoch=epoch,
            observed_at=self.observed_at,
            accepted_collection_slots=self.accepted_collection,
            accepted_verifier_slots=self.accepted_verifier,
        )


def _args(tmp_path: Path, phase: str, predecessor: Path) -> argparse.Namespace:
    return argparse.Namespace(
        phase=phase,
        database_url_env="DATABASE_URL",
        expected_sha=SHA,
        expected_plan_sha256=PLAN,
        activation_nonce=NONCE,
        predecessor_receipt=str(predecessor),
        epoch_id=EPOCH,
        source_id=[SOURCE_A, SOURCE_B],
        json_out=str(tmp_path / f"cadence-{phase}.json"),
        prior_cadence=None,
        activation_chain=None,
    )


def _output(path: str) -> JsonObject:
    return cast("JsonObject", json.loads(Path(path).read_bytes()))


def _run_execute(args: argparse.Namespace, runtime: _Runtime) -> int:
    runner = asyncio.Runner(loop_factory=asyncio.SelectorEventLoop)
    with runner:
        local_loop = runner.get_loop()
        result = runner.run(_execute(args, runtime))
    assert local_loop.is_closed()
    return result


def test_initial_emits_release_manifest_terminal_command(tmp_path: Path) -> None:
    activation = tmp_path / "activation-chain.json"
    _write(
        activation,
        _receipt(
            "materialize-chain",
            {
                "cadence_anchor_at": NOW.isoformat(),
                "binding_sha256": "f" * 64,
                "scope_sha256": "0" * 64,
            },
        ),
    )
    args = _args(tmp_path, "initial", activation)
    args.activation_chain = str(activation)
    runtime = _Runtime()

    assert _run_execute(args, runtime) == 0
    output = _output(args.json_out)
    assert output["command"] == "cadence-initial"
    assert cast("JsonObject", output["details"])["cadence_30d"] == "HOLD"
    assert len(runtime.store.slots) == 3120


def test_status_and_acceptance_emit_truthful_phase_commands(
    tmp_path: Path,
) -> None:
    predecessor = tmp_path / "predecessor.json"
    _write(predecessor, _receipt("final-fan-in", {"lane": "joined"}))
    epoch = CadenceEpoch(
        UUID(EPOCH),
        NOW,
        NOW + timedelta(days=30),
        "1" * 64,
        (UUID(SOURCE_A), UUID(SOURCE_B)),
        "f" * 64,
        "0" * 64,
    )
    status_runtime = _Runtime()
    status_runtime.store.epoch = epoch
    status = _args(tmp_path, "status", predecessor)
    assert _run_execute(status, status_runtime) == 0
    assert _output(status.json_out)["command"] == "cadence-status"

    refresh = tmp_path / "acceptance-refresh.json"
    _write(refresh, _receipt("acceptance-refresh", {"member_count": 15}))
    acceptance = _args(tmp_path, "acceptance", refresh)
    acceptance.prior_cadence = status.json_out
    acceptance_runtime = _Runtime(
        observed_at=epoch.closes_at,
        accepted_collection=240,
        accepted_verifier=2880,
    )
    acceptance_runtime.store.epoch = epoch
    assert _run_execute(acceptance, acceptance_runtime) == 0
    output = _output(acceptance.json_out)
    assert output["command"] == "cadence-acceptance"
    assert cast("JsonObject", output["details"])["cadence_30d"] == "PASS"


def test_local_runner_preserves_the_callers_current_loop(tmp_path: Path) -> None:
    activation = tmp_path / "activation-chain.json"
    _write(
        activation,
        _receipt(
            "materialize-chain",
            {
                "cadence_anchor_at": NOW.isoformat(),
                "binding_sha256": "f" * 64,
                "scope_sha256": "0" * 64,
            },
        ),
    )
    args = _args(tmp_path, "initial", activation)
    args.activation_chain = str(activation)
    def exercise_in_isolated_thread() -> None:
        sentinel = asyncio.new_event_loop()
        asyncio.set_event_loop(sentinel)
        try:
            assert _run_execute(args, _Runtime()) == 0
            assert asyncio.get_event_loop() is sentinel
            assert not sentinel.is_closed()
        finally:
            asyncio.set_event_loop(None)
            sentinel.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(exercise_in_isolated_thread).result()
