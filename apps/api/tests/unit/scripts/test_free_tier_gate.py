from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    from apps.api.scripts import free_tier_gate as gate
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[5]))
    gate = importlib.import_module("apps.api.scripts.free_tier_gate")


class FreeTierVerifier(Protocol):
    @staticmethod
    def require_content_addressed(document: gate.JsonObject, label: str) -> None: ...

    @staticmethod
    def require_measurement_contract(
        manifest: gate.JsonObject,
        measurements: gate.JsonObject,
        production: gate.JsonObject,
    ) -> None: ...


class FreeTierProjection(Protocol):
    @staticmethod
    def expected_window_ids(*, kind: str, captured_at: str) -> frozenset[str]: ...


free_tier_verifier = cast(
    "FreeTierVerifier",
    cast("object", importlib.import_module("apps.api.scripts.free_tier_verifier")),
)
free_tier_projection = cast(
    "FreeTierProjection",
    cast("object", importlib.import_module("apps.api.scripts.free_tier_projection")),
)
free_tier_captures = importlib.import_module("apps.api.scripts.free_tier_captures")
CAPTURE_FIELDS = cast(
    "frozenset[str]",
    importlib.import_module(
        "apps.api.scripts.free_tier_evidence_contract"
    ).CAPTURE_FIELDS,
)

MIB = 1024 * 1024
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "free-tier"


def _sha(value: str) -> str:
    return sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _private_windows_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        monkeypatch.setattr(
            free_tier_captures,
            "_windows_acl_owner_only",
            lambda _path: True,
        )


def _write_deployment_root(
    tmp_path: Path,
    *,
    plan_sha: str = "b" * 64,
    command: str = "deployment-prestate",
) -> Path:
    path = tmp_path / f"{command}.json"
    common: gate.JsonObject = {
        "reviewed_sha": "a" * 40,
        "approved_plan_sha256": plan_sha,
        "activation_nonce": "11111111-1111-4111-8111-111111111111",
    }
    if command == "bootstrap-verify":
        root: gate.JsonObject = {
            "schema_version": 1,
            "command": command,
            "attempt": 1,
            **common,
            "approval_round_id": "c" * 64,
            "approval_launch_sha256s": ["d" * 64, "e" * 64],
            "dispatch_nonce": "22222222-2222-4222-8222-222222222222",
            "run_id": 123,
            "artifact_sha256": "f" * 64,
            "review_root_sha256": "1" * 64,
            "no_spend_receipt_sha256": "2" * 64,
            "backup_sha256": "3" * 64,
            "state_before": "20260726_0009",
            "state_after": "20260727_0010",
            "ledger_exists": True,
            "manifold_data_exists": False,
            "enum_residue": False,
            "accepted": True,
            "terminal_for_attempt": True,
            "retry_permitted": False,
            "predecessor_receipt_sha256": "4" * 64,
        }
    elif command == "aggregate":
        root = gate.with_receipt_sha(
            {
                "schema": "release-chain-receipt.v1",
                "command": command,
                **common,
                "approval_round_id": "c" * 64,
                "approval_launch_sha256s": ["d" * 64, "e" * 64],
                "dispatch_nonce": None,
                "attempt": 0,
                "database_timestamps": {"created_at_db": "2026-07-29T03:00:00Z"},
                "accepted": True,
                "terminal_for_attempt": True,
                "retry_permitted": False,
                "predecessor_receipt_sha256": "1" * 64,
                "details": {
                    "status": "HOLD",
                    "fan_in_sha256": "2" * 64,
                    "cadence_sha256": "3" * 64,
                    "f4_sha256": "4" * 64,
                    "acceptance_refresh_sha256": None,
                },
            }
        )
    else:
        root = {"command": command, "accepted": True, **common}
    _ = path.write_text(json.dumps(root), encoding="utf-8")
    return path


def _private_github_capture_inputs(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, gate.JsonObject]:
    verified = cast(
        "gate.JsonObject",
        json.loads((FIXTURES / "github-verified.json").read_text(encoding="utf-8")),
    )
    observation: gate.JsonObject = {
        "schema": "free-tier.provider-observation.v1",
        "provider": verified["provider"],
        "public_project": verified["public_project"],
        "captured_at": verified["captured_at"],
        "plan": verified["plan"],
        "paid_enabled": verified["paid_enabled"],
        "overage_enabled": verified["overage_enabled"],
        "quota_status": verified["quota_status"],
        "dimensions": verified["dimensions"],
        "source_url_class": verified["source_url_class"],
        "source_url": (
            "https://api.github.com/repos/private-account/private-repo"
            "?access_token=private-url-secret-sentinel"
        ),
    }
    observation_path = tmp_path / "observation.json"
    raw_response_path = tmp_path / "raw-response.json"
    screenshot_path = tmp_path / "screenshot.png"
    output_path = tmp_path / "github-redacted.json"
    captured = datetime.fromisoformat(cast("str", verified["captured_at"]))
    current: dict[str, int] = {}
    for raw in cast("list[gate.JsonObject]", verified["dimensions"]):
        start = datetime.fromisoformat(cast("str", raw["window_start"]))
        end = datetime.fromisoformat(cast("str", raw["window_end"]))
        if start <= captured < end:
            current[cast("str", raw["name"])] = cast("int", raw["observed_usage"])
    billing_items: list[dict[str, object]] = []
    for product, sku, unit, dimension in (
        ("Actions", "actions_linux", "minutes", "github_actions_minutes"),
        (
            "Actions",
            "actions_storage",
            "gigabyte-hours",
            "github_artifact_gb_hours",
        ),
        (
            "Packages",
            "packages_storage",
            "gigabyte-hours",
            "github_packages_gb_hours",
        ),
    ):
        usage = current[dimension]
        billing_items.append(
            {
                "product": product,
                "sku": sku,
                "unitType": unit,
                "pricePerUnit": 0,
                "grossQuantity": usage,
                "grossAmount": 0,
                "discountQuantity": 0,
                "discountAmount": 0,
                "netQuantity": usage,
                "netAmount": 0,
            }
        )
    _ = observation_path.write_text(json.dumps(observation), encoding="utf-8")
    _ = raw_response_path.write_text(
        json.dumps(
            {
                "schema": "free-tier.provider-private-response.v1",
                "provider": "github",
                "observation_sha256": gate.sha256_hex(
                    gate.canonical_bytes(observation)
                ),
                "official_payloads": [
                    {
                        "kind": "repository",
                        "value": {
                            "id": "private",
                            "full_name": "63amg0010-cpu/prediction-market-monitor",
                            "private": False,
                        },
                    },
                    {"kind": "artifacts", "value": []},
                    {
                        "kind": "cache-usage",
                        "value": {
                            "active_caches_size_in_bytes": current[
                                "github_cache_bytes"
                            ],
                            "active_caches_count": 0,
                        },
                    },
                    {
                        "kind": "billing-summary",
                        "request_scope": {
                            "year": captured.year,
                            "month": captured.month,
                            "repository": "63amg0010-cpu/prediction-market-monitor",
                        },
                        "time_period": {
                            "year": captured.year,
                            "month": captured.month,
                        },
                        "value": billing_items,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _ = screenshot_path.write_bytes(b"private-screenshot-secret-sentinel")
    for path in (observation_path, raw_response_path, screenshot_path):
        path.chmod(0o600)
    return (
        observation_path,
        raw_response_path,
        screenshot_path,
        output_path,
        observation,
    )


def _materialize_argv(  # noqa: PLR0913
    *,
    observation: Path,
    raw_response: Path,
    screenshot: Path,
    output: Path,
    predecessor: Path,
    identity_env: str = "GITHUB_REPOSITORY_ID",
    phase: str | None = None,
) -> list[str]:
    args = [
        "materialize-provider-capture",
        "--provider",
        "github",
        "--observation",
        str(observation),
        "--raw-response",
        str(raw_response),
        "--screenshot",
        str(screenshot),
        "--identity-env",
        identity_env,
        "--expected-sha",
        "a" * 40,
        "--expected-plan-sha256",
        "b" * 64,
        "--activation-nonce",
        "11111111-1111-4111-8111-111111111111",
        "--predecessor-receipt",
        str(predecessor),
        "--json-out",
        str(output),
    ]
    if phase is not None:
        args.extend(("--phase", phase))
    return args


def test_materialize_provider_capture_writes_only_redacted_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observation, raw_response, screenshot, output, _ = _private_github_capture_inputs(
        tmp_path
    )
    protected_identity = "private"
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", protected_identity)
    predecessor = _write_deployment_root(tmp_path)

    assert (
        gate.main(
            _materialize_argv(
                observation=observation,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor,
            )
        )
        == 0
    )

    materialized = cast(
        "gate.JsonObject",
        json.loads(output.read_text(encoding="utf-8")),
    )
    redacted = cast("gate.JsonObject", materialized["capture"])
    assert materialized["schema"] == "free-tier.provider-capture-materialized.v1"
    assert redacted["schema"] == "free-tier.provider-capture.v1"
    assert set(redacted) == CAPTURE_FIELDS
    assert redacted["response_sha256"] == gate.sha256_hex(raw_response.read_bytes())
    assert redacted["screenshot_sha256"] == gate.sha256_hex(screenshot.read_bytes())
    assert redacted["source_url_sha256"] == gate.sha256_hex(
        b"https://api.github.com/repos/private-account/private-repo"
        b"?access_token=private-url-secret-sentinel"
    )
    rendered = output.read_text(encoding="utf-8")
    assert protected_identity not in rendered
    assert "actions_linux" not in rendered
    assert "screenshot-secret-sentinel" not in rendered
    assert "private-url-secret-sentinel" not in rendered
    assert "private-account" not in rendered
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    imported = gate.import_provider_capture(
        provider="github",
        input_path=output,
        identity_envs=("GITHUB_REPOSITORY_ID",),
        expected_sha="a" * 40,
        expected_plan_sha256="b" * 64,
        activation_nonce="11111111-1111-4111-8111-111111111111",
        predecessor=gate.load_json(predecessor),
        phase="pre-0010",
    )
    assert imported["schema"] == "free-tier.provider-capture-verified.v1"


def test_materialize_provider_capture_rejects_wrong_identity_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, raw_response, screenshot, output, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("FOREIGN_ID", "private")
    predecessor = _write_deployment_root(tmp_path)
    with pytest.raises(gate.GateHoldError, match="exact protected identity envs"):
        _ = gate.main(
            _materialize_argv(
                observation=observation,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor,
                identity_env="FOREIGN_ID",
            )
        )


@pytest.mark.parametrize(
    ("phase", "command"),
    [
        ("post-0010", "bootstrap-verify"),
        ("acceptance", "aggregate"),
    ],
)
def test_materialize_and_import_accept_later_verified_predecessors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    command: str,
) -> None:
    observation, raw_response, screenshot, output, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")
    predecessor_path = _write_deployment_root(tmp_path, command=command)
    assert (
        gate.main(
            _materialize_argv(
                observation=observation,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor_path,
                phase=phase,
            )
        )
        == 0
    )
    predecessor = gate.load_json(predecessor_path)
    imported = gate.import_provider_capture(
        provider="github",
        input_path=output,
        identity_envs=("GITHUB_REPOSITORY_ID",),
        expected_sha="a" * 40,
        expected_plan_sha256="b" * 64,
        activation_nonce="11111111-1111-4111-8111-111111111111",
        predecessor=predecessor,
        phase=phase,
    )
    assert imported["phase"] == phase
    assert imported["materialization_predecessor_sha256"] == gate.sha256_hex(
        gate.canonical_bytes(predecessor)
    )


def test_materialize_rejects_unsigned_later_phase_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, raw_response, screenshot, output, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")
    predecessor = _write_deployment_root(
        tmp_path,
        command="unsigned-later-node",
    )
    with pytest.raises(gate.GateHoldError, match="predecessor is invalid"):
        _ = gate.main(
            _materialize_argv(
                observation=observation,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor,
                phase="post-0010",
            )
        )


def test_materialize_rejects_minimal_self_hashed_acceptance_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, raw_response, screenshot, output, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")
    minimal = gate.with_receipt_sha(
        {
            "schema": "release-chain-receipt.v1",
            "command": "aggregate",
            "reviewed_sha": "a" * 40,
            "approved_plan_sha256": "b" * 64,
            "activation_nonce": "11111111-1111-4111-8111-111111111111",
            "accepted": True,
            "terminal_for_attempt": True,
            "retry_permitted": False,
        }
    )
    predecessor = tmp_path / "minimal-aggregate.json"
    _ = predecessor.write_text(json.dumps(minimal), encoding="utf-8")
    with pytest.raises(gate.GateHoldError, match="predecessor is invalid"):
        _ = gate.main(
            _materialize_argv(
                observation=observation,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor,
                phase="acceptance",
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("attempt", True),
        ("enum_residue", True),
        ("backup_sha256", None),
    ],
)
def test_materialize_rejects_unsafe_bootstrap_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: gate.JsonValue,
) -> None:
    observation, raw_response, screenshot, output, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")
    predecessor = _write_deployment_root(tmp_path, command="bootstrap-verify")
    document = gate.load_json(predecessor)
    document[field] = value
    _ = predecessor.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(gate.GateHoldError, match="predecessor is invalid"):
        _ = gate.main(
            _materialize_argv(
                observation=observation,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor,
                phase="post-0010",
            )
        )


def test_materialize_provider_capture_rejects_deployment_root_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, raw_response, screenshot, output, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private-repository-id-sentinel")
    predecessor = _write_deployment_root(tmp_path, plan_sha="c" * 64)

    with pytest.raises(gate.GateHoldError, match="predecessor binding mismatch"):
        _ = gate.main(
            _materialize_argv(
                observation=observation,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor,
            )
        )


def test_materialize_provider_capture_rejects_extra_or_unofficial_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation_path, raw_response, screenshot, output, observation = (
        _private_github_capture_inputs(tmp_path)
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")
    predecessor = _write_deployment_root(tmp_path)
    observation["protected_account_id"] = "must-not-pass"
    _ = observation_path.write_text(json.dumps(observation), encoding="utf-8")
    observation_path.chmod(0o600)
    with pytest.raises(gate.GateHoldError, match="schema is not closed"):
        _ = gate.main(
            _materialize_argv(
                observation=observation_path,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor,
            )
        )
    del observation["protected_account_id"]
    observation["source_url"] = "https://example.com/private"
    _ = observation_path.write_text(json.dumps(observation), encoding="utf-8")
    response_document = cast(
        "gate.JsonObject",
        json.loads(raw_response.read_text(encoding="utf-8")),
    )
    response_document["observation_sha256"] = gate.sha256_hex(
        gate.canonical_bytes(observation)
    )
    _ = raw_response.write_text(json.dumps(response_document), encoding="utf-8")
    observation_path.chmod(0o600)
    raw_response.chmod(0o600)
    with pytest.raises(gate.GateHoldError, match="URL is not official"):
        _ = gate.main(
            _materialize_argv(
                observation=observation_path,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=predecessor,
            )
        )


def test_materialize_provider_capture_rejects_unrelated_official_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, raw_response, screenshot, _, document = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")
    document["paid_enabled"] = True
    _ = observation.write_text(json.dumps(document), encoding="utf-8")
    observation.chmod(0o600)
    with pytest.raises(gate.GateHoldError, match="does not derive observation"):
        _ = gate.materialize_provider_capture(
            provider="github",
            observation_path=observation,
            raw_response_path=raw_response,
            screenshot_path=screenshot,
            identity_envs=("GITHUB_REPOSITORY_ID",),
        )


def test_materialize_provider_capture_rejects_official_counter_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, raw_response, screenshot, _, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")
    response = cast(
        "gate.JsonObject", json.loads(raw_response.read_text(encoding="utf-8"))
    )
    payloads = cast("list[gate.JsonObject]", response["official_payloads"])
    items = cast("list[gate.JsonObject]", payloads[3]["value"])
    items[0]["netQuantity"] = cast("int", items[0]["netQuantity"]) + 1
    _ = raw_response.write_text(json.dumps(response), encoding="utf-8")
    raw_response.chmod(0o600)
    with pytest.raises(
        gate.GateHoldError, match="official counters do not derive observation"
    ):
        _ = gate.materialize_provider_capture(
            provider="github",
            observation_path=observation,
            raw_response_path=raw_response,
            screenshot_path=screenshot,
            identity_envs=("GITHUB_REPOSITORY_ID",),
        )


def test_materialize_provider_capture_rejects_unsafe_private_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, raw_response, screenshot, _, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")

    if os.name == "nt":
        monkeypatch.setattr(
            free_tier_captures,
            "_windows_acl_owner_only",
            lambda _path: False,
        )
        message = "Windows ACL"
    else:
        observation.chmod(0o640)
        message = "POSIX ownership or mode"
    with pytest.raises(gate.GateHoldError, match=message):
        _ = gate.materialize_provider_capture(
            provider="github",
            observation_path=observation,
            raw_response_path=raw_response,
            screenshot_path=screenshot,
            identity_envs=("GITHUB_REPOSITORY_ID",),
        )


def test_materialize_provider_capture_rejects_existing_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observation, raw_response, screenshot, output, _ = _private_github_capture_inputs(
        tmp_path
    )
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "private")
    _ = output.write_text("existing-private-alias", encoding="utf-8")
    with pytest.raises(gate.GateHoldError, match="output path is unsafe"):
        _ = gate.main(
            _materialize_argv(
                observation=observation,
                raw_response=raw_response,
                screenshot=screenshot,
                output=output,
                predecessor=_write_deployment_root(tmp_path),
            )
        )


def test_fixture_is_exactly_4800_distinct_rows_and_60_mib() -> None:
    # Given: the reviewed deterministic migration-QA fixture.

    # When: its title/body rows are materialized.
    rows = gate.fixture_rows()

    # Then: row identity and UTF-8 byte totals are exact.
    assert len(rows) == 4_800
    assert len(set(rows)) == 4_800
    measured_bytes = sum(
        len(title.encode()) + len(body.encode()) for title, body in rows
    )
    assert measured_bytes == 60 * MIB


@pytest.mark.parametrize(
    ("actual_bytes", "expected_raw"),
    [
        (59 * MIB, 180 * MIB),
        (60 * MIB, 180 * MIB),
        (61 * MIB, 183 * MIB),
    ],
)
def test_search_projection_uses_60_mib_floor_once(
    actual_bytes: int,
    expected_raw: int,
) -> None:
    # Given: a raw measured amplification below the mandatory 3.0 floor.
    # When: search disk cost is projected.
    projection = gate.search_projection(actual_bytes, 2.5)

    # Then: max(actual, 60 MiB) and exactly one 1.25 inflation are visible.
    assert projection["raw_added_bytes"] == expected_raw
    assert projection["inflated_added_bytes"] == (expected_raw * 5 + 3) // 4


def test_full_page_bound_is_charged_to_every_nonempty_overlap() -> None:
    # Given: a one-second overlap and trailing traffic below the 10k floor.
    capture = datetime(2026, 7, 28, tzinfo=UTC)

    # When: the provider window projection is calculated.
    projected = gate.page_bound_for_window(
        capture_at=capture,
        window_start=capture - timedelta(hours=1),
        window_end=capture + timedelta(seconds=1),
        trailing_30d_page_requests=1,
    )

    # Then: the result is the full U, never a prorated fraction.
    assert projected == 10_000


@pytest.mark.parametrize(
    ("kind", "captured_at", "expected_count"),
    [
        ("rolling-hour", "2026-07-28T00:00:00Z", 720),
        ("rolling-hour", "2026-07-28T00:59:59Z", 721),
        ("rolling-day", "2026-07-28T00:00:00Z", 30),
        ("rolling-day", "2026-07-28T23:59:59Z", 31),
        ("rolling-week", "2026-07-27T00:00:00Z", 5),
        ("rolling-week", "2026-07-28T00:00:00Z", 5),
        ("billing-month", "2026-07-28T00:00:00Z", 2),
    ],
)
def test_expected_windows_cover_partial_hour_day_week_and_month_boundaries(
    kind: str,
    captured_at: str,
    expected_count: int,
) -> None:
    # Given: the 30-day activation horizon starts at an arbitrary instant.
    # When: provider-aligned windows are enumerated.
    windows = free_tier_projection.expected_window_ids(
        kind=kind,
        captured_at=captured_at,
    )

    # Then: partial boundary overlap is included and never prorated away.
    assert len(windows) == expected_count


def test_ratio_at_70_percent_is_hold() -> None:
    # Given: usage exactly at the strict threshold.
    # When / Then: equality is rejected.
    with pytest.raises(gate.GateHoldError, match="strictly below 70%"):
        _ = gate.dimension_result(observed=60, added_raw=8, quota=100)


def test_measure_production_query_is_aggregate_read_only_and_unsampled() -> None:
    # Given: the SQL contract used by Production measurement.
    # When: its statements are inspected.
    statements = gate.production_statements()
    joined = "\n".join(statements).lower()

    # Then: one read-only repeatable-read transaction emits aggregates only.
    assert statements[0] == (
        "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    )
    assert joined.count("transaction_timestamp()") == 1
    assert "sum(octet_length(coalesce(title,''))" in joined
    assert "pg_database_size(current_database())" in joined
    assert "pg_total_relation_size" in joined
    assert " limit " not in joined
    assert "tablesample" not in joined
    assert "select title" not in joined
    assert "select body" not in joined


def test_provider_capture_rejects_direct_unmaterialized_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a hand-authored redacted capture without a root-bound materialization.
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", "expected")
    capture: gate.JsonObject = {
        "schema": "free-tier.provider-capture.v1",
        "provider": "github",
        "public_project": "63amg0010-cpu/prediction-market-monitor",
        "identity_sha256": "0" * 64,
        "identity_bindings": [{"role": "repository", "sha256": _sha("expected")}],
        "captured_at": "2026-07-28T00:00:00Z",
        "plan": "public-standard",
        "paid_enabled": False,
        "overage_enabled": False,
        "quota_status": "known",
        "dimensions": [],
        "response_sha256": "1" * 64,
        "screenshot_sha256": "2" * 64,
        "source_url_class": "official-provider-api-or-dashboard",
        "source_url": "https://docs.github.com/en/billing",
        "source_url_sha256": "3" * 64,
    }
    capture_path = tmp_path / "capture.json"
    _ = capture_path.write_text(json.dumps(capture), encoding="utf-8")

    # When / Then: import fails before trusting any hand-authored fields.
    with pytest.raises(gate.GateHoldError, match="deployment root receipt"):
        _ = gate.import_provider_capture(
            provider="github",
            input_path=capture_path,
            identity_envs=("GITHUB_REPOSITORY_ID",),
            expected_sha="a" * 40,
            phase="pre-0010",
        )


def test_provider_capture_rejects_wrong_identity_env_names_before_hashing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a capture with a self-consistent digest for an arbitrary env name.
    monkeypatch.setenv("ARBITRARY_PROJECT_ID", "foreign")
    capture: gate.JsonObject = {
        "schema": "free-tier.provider-capture.v1",
        "provider": "github",
        "public_project": "63amg0010-cpu/prediction-market-monitor",
        "identity_sha256": gate.sha256_hex(b"foreign"),
        "identity_bindings": [{"role": "repository", "sha256": _sha("foreign")}],
        "captured_at": "2026-07-28T00:00:00Z",
        "plan": "public-standard",
        "paid_enabled": False,
        "overage_enabled": False,
        "quota_status": "known",
        "dimensions": [],
        "response_sha256": "1" * 64,
        "screenshot_sha256": "2" * 64,
        "source_url_class": "official-provider-api-or-dashboard",
        "source_url": "https://docs.github.com/en/billing",
        "source_url_sha256": "3" * 64,
    }
    capture_path = tmp_path / "capture.json"
    _ = capture_path.write_text(json.dumps(capture), encoding="utf-8")

    # When / Then: provider-specific env names fail before digest comparison.
    with pytest.raises(gate.GateHoldError, match="exact protected identity envs"):
        _ = gate.import_provider_capture(
            provider="github",
            input_path=capture_path,
            identity_envs=("ARBITRARY_PROJECT_ID",),
            expected_sha="a" * 40,
            phase="pre-0010",
        )


@pytest.mark.parametrize(
    "identity_envs",
    [
        (),
        ("VERCEL_API_PROJECT_ID", "VERCEL_ORG_ID"),
        ("VERCEL_ORG_ID",),
        ("VERCEL_ORG_ID", "VERCEL_API_PROJECT_ID", "EXTRA_ID"),
    ],
)
def test_provider_capture_rejects_wrong_identity_env_count_or_order(
    tmp_path: Path,
    identity_envs: tuple[str, ...],
) -> None:
    # Given: the vercel-api provider has exactly two protected env inputs.
    capture_path = tmp_path / "unused.json"

    # When / Then: wrong count or order fails before reading the capture body.
    with pytest.raises(gate.GateHoldError, match="exact protected identity envs"):
        _ = gate.import_provider_capture(
            provider="vercel-api",
            input_path=capture_path,
            identity_envs=identity_envs,
            expected_sha="a" * 40,
            phase="pre-0010",
        )


def test_receipt_hash_is_content_addressed() -> None:
    # Given: an unhashed schema-closed receipt body.
    body: gate.JsonObject = {
        "schema": "free-tier.test.v1",
        "accepted": True,
    }

    # When: provenance is attached.
    receipt = gate.with_receipt_sha(body)

    # Then: the digest is reproducible over the body without its own hash.
    digest = receipt.pop("receipt_sha256")
    assert digest == gate.sha256_hex(gate.canonical_bytes(receipt))


def test_canonical_bytes_matches_cross_tool_jcs_number_vector() -> None:
    # Given: values where sorted orjson diverges from ECMAScript/JCS numbers.
    value: gate.JsonObject = {
        "numbers": [1.0, 1e-6, 1e-7, 1e20, 1e21],
        "literals": [None, True, False],
    }
    # When: the receipt boundary applies RFC 8785 canonicalization.
    rendered = gate.canonical_bytes(value)
    # Then: bytes equal JSON.stringify-compatible JCS output.
    assert rendered == (
        b'{"literals":[null,true,false],"numbers":'
        b"[1,0.000001,1e-7,100000000000000000000,1e+21]}"
    )


def test_verifier_rejects_content_address_drift() -> None:
    # Given: a receipt whose body changed after its digest was computed.
    receipt = gate.with_receipt_sha(
        {"schema": "free-tier.test.v1", "reviewed_sha": "a" * 40}
    )
    receipt["reviewed_sha"] = "b" * 40

    # When/Then: verification fails before trusting any projected operands.
    with pytest.raises(gate.GateHoldError, match="receipt SHA mismatch"):
        free_tier_verifier.require_content_addressed(receipt, "test")


def test_verifier_rejects_writable_or_sampled_production_aggregate() -> None:
    # Given: otherwise exact manifest/local operands and an unsafe DB aggregate.
    manifest: gate.JsonObject = {
        "schema": "free-tier.quota-manifest.v1",
        "phase": "pre-0010",
        "reviewed_sha": "a" * 40,
        "threshold_exclusive": 0.7,
        "capture_max_age_seconds_exclusive": 7_200,
        "required_providers": ["github", "vercel-api", "vercel-web", "supabase"],
        "artifact_retention_hours": {
            "activation_evidence": 24,
            "migration_backup_ciphertext": 168,
            "ci_test_build_outputs": 24,
            "local_nonproduction_playwright": 24,
            "rollback_receipts": 168,
            "cadence_receipts": 744,
        },
        "dimensions": [],
        "receipt_sha256": "a" * 64,
    }
    local: gate.JsonObject = {
        "fixture_row_count": 4_800,
        "fixture_title_body_utf8_bytes": 60 * MIB,
        "page_request_equivalent": 10_000,
        "instrumented_http_calls": 2,
    }

    # When/Then: either writable or sampled evidence is independently rejected.
    with pytest.raises(gate.GateHoldError, match="writable"):
        free_tier_verifier.require_measurement_contract(
            manifest, local, {"transaction_read_only": False, "sampled": False}
        )
    with pytest.raises(gate.GateHoldError, match="sampled"):
        free_tier_verifier.require_measurement_contract(
            manifest, local, {"transaction_read_only": True, "sampled": True}
        )
