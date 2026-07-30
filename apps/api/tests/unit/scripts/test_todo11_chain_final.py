from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest
from app.services.release.receipts import canonicalize

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from apps.api.scripts.release_chain import (
    AcceptanceCaptureRequest,
    AcceptanceInputManifestRequest,
    AcceptanceRefreshRequest,
    AggregateRequest,
    CaptureObservation,
    FinalFanInRequest,
    FinalLaneRequest,
    MaterializeChainRequest,
    NamedPath,
    PathReceiptIO,
    ReleaseChainError,
    handle_acceptance_capture,
    handle_acceptance_input_manifest,
    handle_acceptance_refresh,
    handle_aggregate,
    handle_final_fan_in,
    handle_final_lane,
    handle_materialize_chain,
    validate_chain_manifest,
)
from apps.api.scripts.release_chain_acceptance import CURRENT_NAMES, INPUT_NAMES
from apps.api.scripts.release_chain_common import JsonObject, JsonValue

# ruff: noqa: PLR0913, TC002

NOW = datetime(2026, 7, 29, 3, 0, tzinfo=UTC)
SHA = "a" * 40
PLAN = "b" * 64
ROUND = "c" * 64
LAUNCHES: list[JsonValue] = ["d" * 64, "e" * 64]
NONCE = "11111111-1111-4111-8111-111111111111"
IO = PathReceiptIO()
ROOT = Path(__file__).resolve().parents[5]


def _receipt(
    command: str,
    predecessor: JsonObject | None,
    *,
    accepted: bool = True,
    retry: bool = False,
    attempt: int = 0,
    details: JsonObject | None = None,
) -> JsonObject:
    body: JsonObject = {
        "schema": "release-chain-receipt.v1",
        "command": command,
        "reviewed_sha": SHA,
        "approved_plan_sha256": PLAN,
        "approval_round_id": ROUND,
        "approval_launch_sha256s": LAUNCHES,
        "activation_nonce": NONCE,
        "dispatch_nonce": None,
        "attempt": attempt,
        "database_timestamps": {"created_at_db": "2026-07-29T03:00:00Z"},
        "accepted": accepted,
        "terminal_for_attempt": True,
        "retry_permitted": retry,
        "predecessor_receipt_sha256": (
            predecessor["receipt_sha256"] if predecessor else None
        ),
    }
    if details is not None:
        body["details"] = details
    return {**body, "receipt_sha256": sha256(canonicalize(body)).hexdigest()}


def _write(path: Path, value: JsonObject) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(canonicalize(value))


def _step(
    step_id: str,
    command: str,
    kind: str,
    *,
    path: str | None = None,
    attempt_root: str | None = None,
    node_paths: list[str] | None = None,
) -> JsonObject:
    attempt = kind == "attempt"
    grammar: list[JsonValue] = (
        ["accepted-1", "failed-1-accepted-2"] if attempt else []
    )
    kinds: list[JsonValue] = ["verified"] if attempt else []
    paths: list[JsonValue] = list(node_paths or [])
    return {
        "id": step_id,
        "command": command,
        "kind": kind,
        "path": path,
        "attempt_root": attempt_root,
        "node_paths": paths,
        "node_kinds": kinds,
        "required": True,
        "min_attempts": 1 if attempt else None,
        "max_attempts": 2 if attempt else None,
        "branch_grammar": grammar,
        "operation": command,
        "project_kind": None,
        "retry_safety_class": "failed-terminal-db-safe-only",
        "expected_predecessor_rule": "previous-or-failed-attempt-1",
    }


@pytest.mark.parametrize("retry_branch", [False, True])
def test_materialize_selects_only_legal_attempt_branches(
    tmp_path: Path,
    retry_branch: bool,
) -> None:
    root = _receipt("vercel-prestate", None)
    first = _receipt(
        "binding-verified",
        root,
        accepted=not retry_branch,
        retry=retry_branch,
        attempt=1,
    )
    terminal = (
        _receipt("binding-verified", first, attempt=2) if retry_branch else first
    )
    _write(tmp_path / "deployment-prestate.json", root)
    _write(tmp_path / "binding/attempt-1/verified.json", first)
    if retry_branch:
        _write(tmp_path / "binding/attempt-2/verified.json", terminal)
    manifest = {
        "schema": "release-chain.v1",
        "segments": [
            {
                "name": "bootstrap",
                "steps": [
                    _step(
                        "deployment-prestate",
                        "vercel-prestate",
                        "direct",
                        path="deployment-prestate.json",
                    )
                ],
            },
            {
                "name": "normal",
                "steps": [
                    _step(
                        "binding",
                        "binding-verified",
                        "attempt",
                        attempt_root="binding",
                        node_paths=["verified.json"],
                    )
                ],
            },
        ],
    }
    _write(tmp_path / "manifest.json", cast("JsonObject", manifest))
    predecessor = (
        tmp_path
        / f"binding/attempt-{2 if retry_branch else 1}/verified.json"
    )
    result = handle_materialize_chain(
        MaterializeChainRequest(
            tmp_path / "manifest.json",
            tmp_path,
            "binding-verified",
            SHA,
            PLAN,
            NONCE,
            predecessor,
            tmp_path / "activation-chain.json",
        ),
        io=IO,
        clock=lambda: NOW,
    )
    details = cast("JsonObject", result["details"])
    assert details["node_count"] == (3 if retry_branch else 2)
    nodes = cast("list[JsonObject]", details["nodes"])
    assert [node["ordinal"] for node in nodes] == list(
        range(1, len(nodes) + 1)
    )


def test_materialize_rejects_attempt_2_after_accepted_attempt_1(
    tmp_path: Path,
) -> None:
    root = _receipt("vercel-prestate", None)
    first = _receipt("binding-verified", root, attempt=1)
    second = _receipt("binding-verified", first, attempt=2)
    for path, receipt in (
        ("deployment-prestate.json", root),
        ("binding/attempt-1/verified.json", first),
        ("binding/attempt-2/verified.json", second),
    ):
        _write(tmp_path / path, receipt)
    manifest: JsonObject = {
        "schema": "release-chain.v1",
        "segments": [
            {
                "name": "bootstrap",
                "steps": [
                    _step(
                        "deployment-prestate",
                        "vercel-prestate",
                        "direct",
                        path="deployment-prestate.json",
                    )
                ],
            },
            {
                "name": "normal",
                "steps": [
                    _step(
                        "binding",
                        "binding-verified",
                        "attempt",
                        attempt_root="binding",
                        node_paths=["verified.json"],
                    )
                ],
            },
        ],
    }
    _write(tmp_path / "manifest.json", manifest)
    with pytest.raises(ReleaseChainError, match="extra_attempt"):
        _ = handle_materialize_chain(
            MaterializeChainRequest(
                tmp_path / "manifest.json",
                tmp_path,
                "binding-verified",
                SHA,
                PLAN,
                NONCE,
                tmp_path / "binding/attempt-1/verified.json",
                tmp_path / "out.json",
            ),
            io=IO,
            clock=lambda: NOW,
        )


def test_committed_chain_manifests_are_canonical_closed_and_segmented() -> None:
    fixture_root = ROOT / "apps/api/tests/fixtures/release-gate"
    production = validate_chain_manifest(
        IO, fixture_root / "production-chain-manifest.json"
    )
    release = validate_chain_manifest(
        IO, fixture_root / "release-chain-manifest.json"
    )
    production_segments = cast("list[JsonObject]", production["segments"])
    release_segments = cast("list[JsonObject]", release["segments"])
    assert [segment["name"] for segment in production_segments] == [
        "bootstrap",
        "normal",
    ]
    assert [segment["name"] for segment in release_segments] == ["release"]


def test_parallel_final_lanes_fan_in_then_cadence_f4_and_aggregate(
    tmp_path: Path,
) -> None:
    production = _receipt("production", None)
    _write(tmp_path / "production.json", production)
    branches: list[Path] = []
    for lane in ("F1", "F2", "F3"):
        report = tmp_path / f"{lane}.md"
        _ = report.write_text("APPROVE\nredacted evidence\n", encoding="utf-8")
        aux = tmp_path / "playwright.json" if lane == "F3" else None
        if aux:
            _ = aux.write_text('{"accepted":true}', encoding="utf-8")
        output = tmp_path / f"final-{lane}.json"
        _ = handle_final_lane(
            FinalLaneRequest(
                lane,
                report,
                tmp_path / "production.json",
                SHA,
                PLAN,
                NONCE,
                tmp_path / "production.json",
                output,
                aux_report=aux,
            ),
            io=IO,
            clock=lambda: NOW,
        )
        branches.append(output)
    fan_in = handle_final_fan_in(
        FinalFanInRequest(
            tmp_path / "production.json",
            tuple(branches),
            ("F1", "F2", "F3"),
            SHA,
            PLAN,
            NONCE,
            tmp_path / "production.json",
            tmp_path / "fan-in.json",
        ),
        io=IO,
        clock=lambda: NOW,
    )
    cadence = _receipt("cadence-status", fan_in)
    _write(tmp_path / "cadence.json", cadence)
    _ = (tmp_path / "F4.md").write_text("APPROVE\nscope\n", encoding="utf-8")
    f4 = handle_final_lane(
        FinalLaneRequest(
            "F4",
            tmp_path / "F4.md",
            tmp_path / "production.json",
            SHA,
            PLAN,
            NONCE,
            tmp_path / "cadence.json",
            tmp_path / "F4.json",
            cadence=tmp_path / "cadence.json",
        ),
        io=IO,
        clock=lambda: NOW,
    )
    result = handle_aggregate(
        AggregateRequest(
            tmp_path / "fan-in.json",
            tmp_path / "F4.json",
            tmp_path / "cadence.json",
            SHA,
            PLAN,
            NONCE,
            tmp_path / "F4.json",
            tmp_path / "final-status.json",
        ),
        io=IO,
        clock=lambda: NOW,
    )
    assert cast("JsonObject", result["details"])["status"] == "HOLD"
    assert f4["predecessor_receipt_sha256"] == cadence["receipt_sha256"]


class _StubCaptures:
    def capture(self, member_name: str) -> CaptureObservation:
        return CaptureObservation(
            sha256(member_name.encode()).hexdigest(),
            NOW - timedelta(minutes=1),
            "stub-v1",
            accepted=True,
        )


def test_acceptance_exact_8_then_15_members_and_refresh(
    tmp_path: Path,
) -> None:
    final_status = _receipt("aggregate", None)
    _write(tmp_path / "final-status.json", final_status)
    leaves: list[NamedPath] = []
    for name in INPUT_NAMES:
        path = tmp_path / "staging" / name
        _write(
            path,
            {
                "schema": "acceptance-leaf.v1",
                "name": name,
                "reviewed_sha": SHA,
                "approved_plan_sha256": PLAN,
                "activation_nonce": NONCE,
            },
        )
        if name == INPUT_NAMES[0]:
            _ = path.write_bytes(path.read_bytes() + b"\n")
        leaves.append(NamedPath(name, path))
    input_set = handle_acceptance_input_manifest(
        AcceptanceInputManifestRequest(
            tuple(leaves),
            SHA,
            PLAN,
            NONCE,
            tmp_path / "final-status.json",
            tmp_path / "captures",
            tmp_path / "input-set.json",
        ),
        io=IO,
        clock=lambda: NOW,
    )
    capture_id = str(input_set["receipt_sha256"])
    free_tier = _receipt("free-tier-verify", input_set)
    free_tier_path = tmp_path / "free-tier-result.json"
    _write(free_tier_path, free_tier)
    copied = tuple(
        NamedPath(name, tmp_path / "captures" / capture_id / name)
        for name in INPUT_NAMES
    )
    current = handle_acceptance_capture(
        AcceptanceCaptureRequest(
            copied,
            tmp_path / "input-set.json",
            free_tier_path,
            tmp_path / "current",
            tmp_path / "current-state.json",
            SHA,
            PLAN,
            NONCE,
            tmp_path / "final-status.json",
        ),
        io=IO,
        clock=lambda: NOW,
        provider=_StubCaptures(),
    )
    members = [
        *copied,
        NamedPath("free-tier-result.json", free_tier_path),
        *(
            NamedPath(name, tmp_path / "current" / name)
            for name in CURRENT_NAMES
        ),
    ]
    refresh = handle_acceptance_refresh(
        AcceptanceRefreshRequest(
            tuple(members),
            tmp_path / "input-set.json",
            tmp_path / "current-state.json",
            15,
            SHA,
            PLAN,
            NONCE,
            tmp_path / "current-state.json",
            tmp_path / "refresh.json",
        ),
        io=IO,
        clock=lambda: NOW,
    )
    assert cast("JsonObject", current["details"])["member_count"] == 15
    assert cast("JsonObject", refresh["details"])["member_count"] == 15
    production = _receipt("production", None)
    fan_in = _receipt("final-fan-in", None)
    cadence = _receipt("cadence-acceptance", refresh)
    for name, receipt in (
        ("production.json", production),
        ("fan-in.json", fan_in),
        ("cadence-acceptance.json", cadence),
    ):
        _write(tmp_path / name, receipt)
    _ = (tmp_path / "F4-acceptance.md").write_text(
        "APPROVE\n30-day scope\n",
        encoding="utf-8",
    )
    f4 = handle_final_lane(
        FinalLaneRequest(
            "F4-acceptance",
            tmp_path / "F4-acceptance.md",
            tmp_path / "production.json",
            SHA,
            PLAN,
            NONCE,
            tmp_path / "cadence-acceptance.json",
            tmp_path / "F4-acceptance.json",
            cadence=tmp_path / "cadence-acceptance.json",
        ),
        io=IO,
        clock=lambda: NOW,
    )
    accepted = handle_aggregate(
        AggregateRequest(
            tmp_path / "fan-in.json",
            tmp_path / "F4-acceptance.json",
            tmp_path / "cadence-acceptance.json",
            SHA,
            PLAN,
            NONCE,
            tmp_path / "F4-acceptance.json",
            tmp_path / "final-status-accepted.json",
            acceptance_refresh=tmp_path / "refresh.json",
        ),
        io=IO,
        clock=lambda: NOW,
    )
    assert f4["predecessor_receipt_sha256"] == cadence["receipt_sha256"]
    assert cast("JsonObject", accepted["details"])["status"] == "COMPLETE"


def test_acceptance_input_rejects_extra_member(tmp_path: Path) -> None:
    predecessor = _receipt("aggregate", None)
    _write(tmp_path / "final-status.json", predecessor)
    members = tuple(
        NamedPath(name, tmp_path / name) for name in (*INPUT_NAMES, "extra.json")
    )
    with pytest.raises(ReleaseChainError, match="manifest_members_not_exact"):
        _ = handle_acceptance_input_manifest(
            AcceptanceInputManifestRequest(
                members,
                SHA,
                PLAN,
                NONCE,
                tmp_path / "final-status.json",
                tmp_path / "captures",
                tmp_path / "input-set.json",
            ),
            io=IO,
            clock=lambda: NOW,
        )
