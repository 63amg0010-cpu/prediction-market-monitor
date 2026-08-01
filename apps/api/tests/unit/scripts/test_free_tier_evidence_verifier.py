"""Free-tier provider evidence verification tests."""

from __future__ import annotations

import sys
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from apps.api.scripts import free_tier_verifier
from apps.api.scripts.free_tier_domain import (
    PROVIDERS,
    GateHoldError,
    JsonObject,
    JsonValue,
    canonical_bytes,
    load_json,
    sha256_hex,
    with_receipt_sha,
    write_json,
)
from apps.api.scripts.release_evidence_contracts import PRE_0010_KINDS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "free-tier"
REVIEWED_SHA = "a" * 40
DB_NOW = datetime(2026, 7, 28, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    manifest: JsonObject
    local: JsonObject
    production: JsonObject
    captures: tuple[JsonObject, ...]


@dataclass(frozen=True, slots=True)
class ProviderStateMutation:
    field: str
    value: str | bool
    message: str


def _rehash(document: JsonObject) -> JsonObject:
    body = {key: value for key, value in document.items() if key != "receipt_sha256"}
    return with_receipt_sha(body)


def _dimensions(captures: tuple[JsonObject, ...]) -> list[JsonObject]:
    dimensions: list[JsonObject] = []
    for capture in captures:
        values = capture["dimensions"]
        assert isinstance(values, list)
        for value in values:
            assert isinstance(value, dict)
            dimensions.append(deepcopy(value))
    return dimensions


def _pre_0010_hashes(bundle: EvidenceBundle) -> tuple[JsonObject, ...]:
    documents = (
        bundle.local,
        bundle.manifest,
        *bundle.captures,
        bundle.production,
    )
    return tuple(
        {
            "schema_version": 1,
            "command": "canonical-hash",
            "input_sha256": sha256_hex(canonical_bytes(document)),
            "accepted": True,
        }
        for document in documents
    )


def _pre_0010_imports(bundle: EvidenceBundle) -> tuple[JsonObject, ...]:
    hashes = _pre_0010_hashes(bundle)
    return tuple(
        {
            "schema_version": 1,
            "command": "evidence-import",
            "kind": kind,
            "reviewed_sha": REVIEWED_SHA,
            "approved_plan_sha256": "b" * 64,
            "activation_nonce": "11111111-1111-4111-8111-111111111111",
            "input_sha256": str(hashed["input_sha256"]),
            "content_addressed_path": (
                f"pre-0010/imports/{kind}/{hashed['input_sha256']}.json"
            ),
            "accepted": True,
            "predecessor_receipt_sha256": sha256_hex(canonical_bytes(hashed)),
        }
        for kind, hashed in zip(PRE_0010_KINDS, hashes, strict=True)
    )


def _pre_0010_join(bundle: EvidenceBundle) -> JsonObject:
    imports = _pre_0010_imports(bundle)
    return {
        "command": "evidence-join",
        "branch_kinds": list(PRE_0010_KINDS),
        "branch_input_sha256s": {
            str(imported["kind"]): str(imported["input_sha256"])
            for imported in imports
        },
        "branch_receipt_sha256s": {
            str(imported["kind"]): sha256_hex(canonical_bytes(imported))
            for imported in imports
        },
    }


@pytest.fixture
def evidence_bundle() -> EvidenceBundle:
    captures = tuple(
        load_json(FIXTURES / f"{provider}-verified.json") for provider in PROVIDERS
    )
    manifest = load_json(FIXTURES / "all-dimensions-below-70.json")
    local = with_receipt_sha(
        {
            "schema": "free-tier.local-measurement.v1",
            "reviewed_sha": REVIEWED_SHA,
            "fixture_row_count": 4_800,
            "fixture_title_body_utf8_bytes": 60 * 1024 * 1024,
            "page_request_equivalent": 10_000,
            "instrumented_http_calls": 2,
            "raw_measured_amplification": 3,
        }
    )
    production = with_receipt_sha(
        {
            "schema": "free-tier.production-measurement.v1",
            "reviewed_sha": REVIEWED_SHA,
            "db_now": "2026-07-28T00:30:00Z",
            "transaction_read_only": True,
            "sampled": False,
            "actual_production_title_body_utf8_bytes": 0,
        }
    )
    return EvidenceBundle(manifest, local, production, captures)


def _execute(  # noqa: PLR0913
    bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    predecessor: JsonObject | None = None,
    imports_override: tuple[JsonObject, ...] | None = None,
    hashes_override: tuple[JsonObject, ...] | None = None,
) -> JsonObject:
    paths = {
        "manifest": tmp_path / "manifest.json",
        "measurements": tmp_path / "local.json",
        "production-measurements": tmp_path / "production.json",
        "predecessor-receipt": tmp_path / "evidence-join.json",
    }
    for label, document in (
        ("manifest", bundle.manifest),
        ("measurements", bundle.local),
        ("production-measurements", bundle.production),
    ):
        write_json(paths[label], document)
    capture_paths: list[str] = []
    for index, capture in enumerate(bundle.captures):
        path = tmp_path / f"capture-{index}.json"
        write_json(path, capture)
        capture_paths.append(str(path))
    import_paths: list[str] = []
    imports = (
        imports_override
        if imports_override is not None
        else _pre_0010_imports(bundle)
        if len(bundle.captures) == 4
        else ()
    )
    for index, imported in enumerate(imports):
        path = tmp_path / f"evidence-import-{index}.json"
        write_json(path, imported)
        import_paths.append(str(path))
    hash_paths: list[str] = []
    hashes = (
        hashes_override
        if hashes_override is not None
        else _pre_0010_hashes(bundle)
        if len(bundle.captures) == 4
        else ()
    )
    for index, hashed in enumerate(hashes):
        path = tmp_path / f"evidence-hash-{index}.json"
        write_json(path, hashed)
        hash_paths.append(str(path))
    default_join = _pre_0010_join(bundle) if len(bundle.captures) == 4 else {}
    write_json(
        paths["predecessor-receipt"],
        default_join if predecessor is None else predecessor,
    )

    def current_database_time(_database_url: str) -> datetime:
        return DB_NOW

    monkeypatch.setenv("FREE_TIER_TEST_DATABASE_URL", "postgresql://redacted")
    monkeypatch.setattr(free_tier_verifier, "database_time", current_database_time)
    return free_tier_verifier.verify_command(
        {
            "database-url-env": "FREE_TIER_TEST_DATABASE_URL",
            "manifest": str(paths["manifest"]),
            "measurements": str(paths["measurements"]),
            "production-measurements": str(paths["production-measurements"]),
            "provider-capture": tuple(capture_paths),
            "evidence-hash": tuple(hash_paths),
            "evidence-import": tuple(import_paths),
            "predecessor-receipt": str(paths["predecessor-receipt"]),
            "expected-sha": REVIEWED_SHA,
            "expected-plan-sha256": "b" * 64,
            "activation-nonce": "11111111-1111-4111-8111-111111111111",
        }
    )


def test_verify_accepts_exact_fresh_four_provider_capture_set(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: all four fresh schema-closed provider captures.
    # When: the real verifier consumes their derived dimensions.
    result = _execute(evidence_bundle, tmp_path, monkeypatch)
    # Then: every capture-derived dimension passes the strict threshold.
    assert result["accepted"] is True
    assert result["manifest_sha256"] == sha256_hex(
        canonical_bytes(evidence_bundle.manifest)
    )
    dimensions = result["dimensions"]
    assert isinstance(dimensions, list)
    assert len(dimensions) == 19


def test_verify_rejects_missing_provider_capture(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: one required provider capture is absent.
    bundle = EvidenceBundle(
        evidence_bundle.manifest,
        evidence_bundle.local,
        evidence_bundle.production,
        evidence_bundle.captures[:-1],
    )
    # When/Then: verification fails before evaluating quota arithmetic.
    with pytest.raises(GateHoldError, match="exactly four"):
        _ = _execute(bundle, tmp_path, monkeypatch)


def test_verify_rejects_manifest_not_joined_by_exact_hash(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    predecessor = _pre_0010_join(evidence_bundle)
    input_hashes = predecessor["branch_input_sha256s"]
    assert isinstance(input_hashes, dict)
    input_hashes["quota-manifest"] = "0" * 64
    with pytest.raises(GateHoldError, match="join input hash mismatch"):
        _ = _execute(
            evidence_bundle,
            tmp_path,
            monkeypatch,
            predecessor=predecessor,
        )


@pytest.mark.parametrize(
    ("field", "kind"),
    [
        ("branch_input_sha256s", "vercel-web-capture"),
        ("branch_receipt_sha256s", "github-capture"),
    ],
)
def test_verify_rejects_provider_capture_missing_from_join_maps(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    kind: str,
) -> None:
    predecessor = _pre_0010_join(evidence_bundle)
    hashes = predecessor[field]
    assert isinstance(hashes, dict)
    del hashes[kind]
    with pytest.raises(GateHoldError, match="join is incomplete"):
        _ = _execute(
            evidence_bundle,
            tmp_path,
            monkeypatch,
            predecessor=predecessor,
        )


def test_verify_rejects_forged_evidence_import_contract(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imports = list(_pre_0010_imports(evidence_bundle))
    forged = deepcopy(imports[2])
    forged["content_addressed_path"] = "pre-0010/imports/github-capture/receipt.json"
    imports[2] = forged
    predecessor = _pre_0010_join(evidence_bundle)
    receipt_hashes = predecessor["branch_receipt_sha256s"]
    assert isinstance(receipt_hashes, dict)
    receipt_hashes["github-capture"] = sha256_hex(canonical_bytes(forged))
    with pytest.raises(GateHoldError, match="evidence import mismatch"):
        _ = _execute(
            evidence_bundle,
            tmp_path,
            monkeypatch,
            predecessor=predecessor,
            imports_override=tuple(imports),
        )


def test_verify_rejects_manifest_operand_substitution(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a rehashed manifest with one operand differing from its capture.
    manifest = deepcopy(evidence_bundle.manifest)
    dimensions = manifest["dimensions"]
    assert isinstance(dimensions, list)
    first = dimensions[0]
    assert isinstance(first, dict)
    first["quota"] = 10_000
    bundle = EvidenceBundle(
        _rehash(manifest),
        evidence_bundle.local,
        evidence_bundle.production,
        evidence_bundle.captures,
    )
    # When/Then: the substituted operand cannot pass on its own valid hash.
    with pytest.raises(GateHoldError, match="do not match provider captures"):
        _ = _execute(bundle, tmp_path, monkeypatch)


def test_verify_rejects_self_consistent_added_usage_undercount(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a capture and manifest that agree on a zero-cost primitive omission.
    capture = deepcopy(evidence_bundle.captures[0])
    values = capture["dimensions"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    first["added_usage_raw"] = 0
    first["projection_operands"] = {
        "traffic": {
            "trailing_30d_page_requests": 0,
            "units_per_page_request": 0,
        },
        "workflow_attempts": [],
        "deployment_attempts": [],
        "artifacts": [],
        "encrypted_backup": {
            "last_successful_encrypted_backup_bytes": 0,
            "current_logical_size_estimate_bytes": 0,
            "attempts": 0,
            "retention_hours": 168,
            "units_per_gib_hour": 1,
        },
    }
    captures = (_rehash(capture), *evidence_bundle.captures[1:])
    manifest = deepcopy(evidence_bundle.manifest)
    dimension_values: list[JsonValue] = list(_dimensions(captures))
    manifest["dimensions"] = dimension_values
    bundle = EvidenceBundle(
        _rehash(manifest),
        evidence_bundle.local,
        evidence_bundle.production,
        captures,
    )

    # When / Then: verifier rejects omitted/non-fan-out primitives before ratio.
    with pytest.raises(GateHoldError, match="traffic fan-out"):
        _ = _execute(bundle, tmp_path, monkeypatch)


def test_verify_rejects_one_window_shorthand_for_rolling_hour(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: only one aligned rolling-hour record for a 30-day horizon.
    capture = deepcopy(evidence_bundle.captures[0])
    values = capture["dimensions"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    first["window_start"] = "2026-07-28T00:00:00Z"
    first["window_end"] = "2026-07-28T01:00:00Z"
    first["window_kind"] = "rolling-hour"
    first["window_id"] = (
        "rolling-hour:2026-07-28T00:00:00Z:2026-07-28T01:00:00Z"
    )
    first["quota"] = 1_000_000
    first_name = first["name"]
    capture["dimensions"] = [
        value
        for value in values
        if not (
            isinstance(value, dict)
            and value is not first
            and value.get("name") == first_name
        )
    ]
    captures = (_rehash(capture), *evidence_bundle.captures[1:])
    manifest = deepcopy(evidence_bundle.manifest)
    dimension_values: list[JsonValue] = list(_dimensions(captures))
    manifest["dimensions"] = dimension_values
    bundle = EvidenceBundle(
        _rehash(manifest),
        evidence_bundle.local,
        evidence_bundle.production,
        captures,
    )

    # When / Then: one selected window cannot stand in for 30-day enumeration.
    with pytest.raises(GateHoldError, match="window set is incomplete"):
        _ = _execute(bundle, tmp_path, monkeypatch)


@pytest.mark.parametrize(
    "mutation",
    [
        ProviderStateMutation(
            field="paid_enabled", value=True, message="paid provider path"
        ),
        ProviderStateMutation(
            field="overage_enabled", value=True, message="overage path"
        ),
        ProviderStateMutation(
            field="quota_status", value="unknown", message="unknown or N/A"
        ),
    ],
)
def test_verify_rejects_non_free_or_unknown_provider_state(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: ProviderStateMutation,
) -> None:
    # Given: a content-addressed capture with a forbidden provider state.
    capture = deepcopy(evidence_bundle.captures[0])
    capture[mutation.field] = mutation.value
    captures = (_rehash(capture), *evidence_bundle.captures[1:])
    bundle = EvidenceBundle(
        evidence_bundle.manifest,
        evidence_bundle.local,
        evidence_bundle.production,
        captures,
    )
    # When/Then: paid, overage, and unknown states each fail closed.
    with pytest.raises(GateHoldError, match=mutation.message):
        _ = _execute(bundle, tmp_path, monkeypatch)


def test_verify_rejects_stale_capture_at_exclusive_boundary(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a capture exactly two hours old.
    capture = deepcopy(evidence_bundle.captures[0])
    capture["captured_at"] = "2026-07-27T23:00:00Z"
    captures = (_rehash(capture), *evidence_bundle.captures[1:])
    bundle = EvidenceBundle(
        evidence_bundle.manifest,
        evidence_bundle.local,
        evidence_bundle.production,
        captures,
    )
    # When/Then: age equality is stale under the exclusive freshness rule.
    with pytest.raises(GateHoldError, match="at least two hours old"):
        _ = _execute(bundle, tmp_path, monkeypatch)


def test_verify_rejects_unofficial_quota_url(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a rehashed provider capture pointing to a non-provider host.
    capture = deepcopy(evidence_bundle.captures[0])
    capture["source_url"] = "https://example.com/quota"
    capture["source_url_sha256"] = (
        "ae7d491977e2629313409ef633052174531243e0ab73fdde41ac59b8b46550f6"
    )
    captures = (_rehash(capture), *evidence_bundle.captures[1:])
    bundle = EvidenceBundle(
        evidence_bundle.manifest,
        evidence_bundle.local,
        evidence_bundle.production,
        captures,
    )
    # When/Then: a correctly hashed unofficial URL is still rejected.
    with pytest.raises(GateHoldError, match="URL is not official"):
        _ = _execute(bundle, tmp_path, monkeypatch)


def test_verify_rejects_ratio_equality_derived_from_capture(
    evidence_bundle: EvidenceBundle,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: capture operands whose observed plus inflated addition equals 70%.
    capture = deepcopy(evidence_bundle.captures[0])
    values = capture["dimensions"]
    assert isinstance(values, list)
    first = values[0]
    assert isinstance(first, dict)
    added = first["added_usage_raw"]
    assert isinstance(added, int)
    first["quota"] = 1_000_000
    first["observed_usage"] = 700_000 - ((added * 5 + 3) // 4)
    captures = (_rehash(capture), *evidence_bundle.captures[1:])
    manifest = deepcopy(evidence_bundle.manifest)
    dimension_values: list[JsonValue] = list(_dimensions(captures))
    manifest["dimensions"] = dimension_values
    bundle = EvidenceBundle(
        _rehash(manifest),
        evidence_bundle.local,
        evidence_bundle.production,
        captures,
    )
    # When/Then: exact threshold equality is rejected from captured operands.
    with pytest.raises(GateHoldError, match="strictly below 70%"):
        _ = _execute(bundle, tmp_path, monkeypatch)
