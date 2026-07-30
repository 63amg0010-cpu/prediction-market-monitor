from __future__ import annotations

import importlib
import json
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

MIB = 1024 * 1024


def _sha(value: str) -> str:
    return sha256(value.encode()).hexdigest()


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


def test_provider_capture_rejects_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a redacted capture bound to a different protected identity.
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

    # When / Then: import fails before producing public evidence.
    with pytest.raises(gate.GateHoldError, match="identity mismatch"):
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
