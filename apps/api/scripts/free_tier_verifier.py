"""Database-time free-tier result verification."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import anyio
from apps.api.scripts.free_tier_captures import validate_verified_capture
from apps.api.scripts.free_tier_db import db_now
from apps.api.scripts.free_tier_domain import (
    ARTIFACT_RETENTION_HOURS,
    FIXTURE_BYTES,
    FIXTURE_ROWS,
    PHASES,
    PROVIDERS,
    STRICT_THRESHOLD,
    GateHoldError,
    JsonObject,
    JsonValue,
    canonical_bytes,
    dimension_result,
    load_json,
    parse_time,
    require_string,
    search_projection,
    sha256_hex,
    with_receipt_sha,
)
from apps.api.scripts.release_evidence_contracts import PRE_0010_KINDS

type Args = dict[str, str | tuple[str, ...] | bool]
MAX_CAPTURE_AGE = timedelta(hours=2)
CAPTURE_MAX_AGE_SECONDS = 7_200
INSTRUMENTED_HTTP_CALLS = 2
PAGE_REQUEST_EQUIVALENT = 10_000
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MANIFEST_FIELDS = frozenset(
    {
        "schema",
        "phase",
        "reviewed_sha",
        "required_providers",
        "artifact_retention_hours",
        "threshold_exclusive",
        "capture_max_age_seconds_exclusive",
        "dimensions",
        "receipt_sha256",
    }
)
OPTIONAL_CHAIN_FIELDS = frozenset(
    {"expected_plan_sha256", "activation_nonce", "predecessor_receipt"}
)


def _string(values: Args, name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value:
        raise GateHoldError(f"--{name} is required")
    return value


def _optional(values: Args, name: str) -> str | None:
    value = values.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise GateHoldError(f"--{name} must be nonempty")
    return value


def _phase(values: Args) -> str:
    value = _optional(values, "phase") or "pre-0010"
    if value not in PHASES:
        raise GateHoldError("--phase is unsupported")
    return value


def _chain_fields(values: Args) -> JsonObject:
    fields: JsonObject = {}
    for name in ("expected-plan-sha256", "activation-nonce", "predecessor-receipt"):
        value = _optional(values, name)
        if value is not None:
            fields[name.replace("-", "_")] = value
    return fields


def _dimension_projection(dimension: JsonObject) -> JsonObject:
    name = require_string(dimension.get("name"), "dimension.name")
    observed = dimension.get("observed_usage")
    added = dimension.get("added_usage_raw")
    quota = dimension.get("quota")
    if (
        not isinstance(observed, int)
        or not isinstance(added, int)
        or not isinstance(quota, int)
    ):
        raise GateHoldError(f"dimension operands are unknown: {name}")
    return {
        "name": name,
        **dimension_result(observed=observed, added_raw=added, quota=quota),
    }


def _ratio_value(result: JsonObject) -> float:
    ratio = result.get("ratio")
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        raise GateHoldError("dimension ratio must be numeric")
    return float(ratio)


def require_content_addressed(document: JsonObject, label: str) -> None:
    """Reject evidence whose canonical body does not match its receipt digest."""
    receipt_sha256 = document.get("receipt_sha256")
    if not isinstance(receipt_sha256, str):
        raise GateHoldError(f"{label} receipt SHA is required")
    body = {key: value for key, value in document.items() if key != "receipt_sha256"}
    if receipt_sha256 != sha256_hex(canonical_bytes(body)):
        raise GateHoldError(f"{label} receipt SHA mismatch")


def require_measurement_contract(
    manifest: JsonObject,
    measurements: JsonObject,
    production: JsonObject,
) -> None:
    """Reject measurements outside the reviewed fixture and read-only contract."""
    if (
        frozenset(set(manifest) - OPTIONAL_CHAIN_FIELDS) != MANIFEST_FIELDS
        or manifest.get("schema") != "free-tier.quota-manifest.v1"
        or manifest.get("threshold_exclusive") != STRICT_THRESHOLD
        or manifest.get("capture_max_age_seconds_exclusive") != CAPTURE_MAX_AGE_SECONDS
        or manifest.get("required_providers") != list(PROVIDERS)
        or manifest.get("artifact_retention_hours") != ARTIFACT_RETENTION_HOURS
    ):
        raise GateHoldError("quota manifest contract mismatch")
    if (
        measurements.get("fixture_row_count") != FIXTURE_ROWS
        or measurements.get("fixture_title_body_utf8_bytes") != FIXTURE_BYTES
        or measurements.get("page_request_equivalent") != PAGE_REQUEST_EQUIVALENT
        or measurements.get("instrumented_http_calls") != INSTRUMENTED_HTTP_CALLS
    ):
        raise GateHoldError("local measurement contract mismatch")
    if production.get("transaction_read_only") is not True:
        raise GateHoldError("Production measurement is writable")
    if production.get("sampled") is not False:
        raise GateHoldError("Production measurement is sampled")


def require_pre_0010_join(  # noqa: PLR0913
    predecessor: JsonObject,
    manifest: JsonObject,
    measurements: JsonObject,
    production: JsonObject,
    captures: list[JsonObject],
    imports: list[JsonObject],
    hashes: list[JsonObject],
    expected_sha: str,
    expected_plan_sha256: str,
    activation_nonce: str,
) -> None:
    """Bind all seven consumed pre-0010 inputs to one evidence join."""
    branch_kinds = predecessor.get("branch_kinds")
    input_hashes = predecessor.get("branch_input_sha256s")
    receipt_hashes = predecessor.get("branch_receipt_sha256s")
    expected_kinds = list(PRE_0010_KINDS)
    if (
        predecessor.get("command") != "evidence-join"
        or branch_kinds != expected_kinds
        or not isinstance(input_hashes, dict)
        or not isinstance(receipt_hashes, dict)
        or set(input_hashes) != set(PRE_0010_KINDS)
        or set(receipt_hashes) != set(PRE_0010_KINDS)
        or any(
            not isinstance(value, str)
            or SHA256_PATTERN.fullmatch(value) is None
            for value in receipt_hashes.values()
        )
    ):
        raise GateHoldError("pre-0010 evidence join is incomplete")
    expected_inputs = {
        "local-measurement": sha256_hex(canonical_bytes(measurements)),
        "quota-manifest": sha256_hex(canonical_bytes(manifest)),
        "github-capture": sha256_hex(canonical_bytes(captures[0])),
        "vercel-api-capture": sha256_hex(canonical_bytes(captures[1])),
        "vercel-web-capture": sha256_hex(canonical_bytes(captures[2])),
        "supabase-capture": sha256_hex(canonical_bytes(captures[3])),
        "production-measurement": sha256_hex(canonical_bytes(production)),
    }
    if input_hashes != expected_inputs:
        raise GateHoldError("pre-0010 evidence join input hash mismatch")
    if len(imports) != len(PRE_0010_KINDS) or len(hashes) != len(PRE_0010_KINDS):
        raise GateHoldError("pre-0010 evidence imports are incomplete")
    actual_receipts: dict[str, str] = {}
    import_fields = {
        "schema_version",
        "command",
        "kind",
        "reviewed_sha",
        "approved_plan_sha256",
        "activation_nonce",
        "input_sha256",
        "content_addressed_path",
        "accepted",
        "predecessor_receipt_sha256",
    }
    hash_fields = {"schema_version", "command", "input_sha256", "accepted"}
    for kind, imported, hashed in zip(
        PRE_0010_KINDS, imports, hashes, strict=True
    ):
        input_sha = expected_inputs[kind]
        content_path = Path(str(imported.get("content_addressed_path", "")))
        if (
            set(imported) != import_fields
            or imported.get("command") != "evidence-import"
            or imported.get("accepted") is not True
            or imported.get("kind") != kind
            or imported.get("input_sha256") != input_sha
            or imported.get("reviewed_sha") != expected_sha
            or imported.get("approved_plan_sha256") != expected_plan_sha256
            or imported.get("activation_nonce") != activation_nonce
            or content_path.parent.name != kind
            or content_path.name != f"{input_sha}.json"
            or set(hashed) != hash_fields
            or hashed.get("schema_version") != 1
            or hashed.get("command") != "canonical-hash"
            or hashed.get("accepted") is not True
            or hashed.get("input_sha256") != input_sha
            or imported.get("predecessor_receipt_sha256")
            != sha256_hex(canonical_bytes(hashed))
        ):
            raise GateHoldError("pre-0010 evidence import mismatch")
        actual_receipts[kind] = sha256_hex(canonical_bytes(imported))
    if receipt_hashes != actual_receipts:
        raise GateHoldError("pre-0010 evidence import receipt mismatch")


def _provider_capture_paths(values: Args) -> tuple[str, ...]:
    captures = values.get("provider-capture", ())
    if not isinstance(captures, tuple) or len(captures) != len(PROVIDERS):
        raise GateHoldError("--provider-capture requires exactly four captures")
    return captures


def _require_fresh(document: JsonObject, now: datetime) -> None:
    captured = parse_time(
        document.get("db_now", document.get("captured_at")), "capture time"
    )
    age = now - captured
    if age < timedelta(0) or age >= MAX_CAPTURE_AGE:
        raise GateHoldError("capture is future-dated or at least two hours old")


def _capture_dimensions(
    captures: list[JsonObject],
    phase: str,
    now: datetime,
) -> list[JsonObject]:
    capture_by_provider: dict[str, list[JsonObject]] = {}
    for capture in captures:
        _require_fresh(capture, now)
        if capture.get("phase") != phase:
            raise GateHoldError("provider capture phase mismatch")
        provider, dimensions = validate_verified_capture(capture)
        if provider in capture_by_provider:
            raise GateHoldError("provider capture is duplicated")
        capture_by_provider[provider] = dimensions
    if set(capture_by_provider) != set(PROVIDERS):
        raise GateHoldError("provider capture set is incomplete")
    return [
        dimension
        for provider in PROVIDERS
        for dimension in capture_by_provider[provider]
    ]


def database_time(database_url: str) -> datetime:
    """Resolve authoritative time at the synchronous CLI boundary."""
    return anyio.run(db_now, database_url)


def verify_command(values: Args) -> JsonObject:  # noqa: C901, PLR0915
    """Verify current captures against one database time and strict ratios."""
    expected_sha = _string(values, "expected-sha")
    environment_name = _string(values, "database-url-env")
    database_url = os.environ.get(environment_name)
    if database_url is None or not database_url:
        raise GateHoldError(f"database environment is empty: {environment_name}")
    now = database_time(database_url)
    manifest = load_json(Path(_string(values, "manifest")))
    measurements = load_json(Path(_string(values, "measurements")))
    production = load_json(Path(_string(values, "production-measurements")))
    captures = _provider_capture_paths(values)
    documents = [manifest, measurements, production]
    documents.extend(load_json(Path(path)) for path in captures)
    if any(document.get("reviewed_sha") != expected_sha for document in documents):
        raise GateHoldError("reviewed SHA provenance drift")
    for label, document in zip(
        ("manifest", "local measurement", "Production measurement", *captures),
        documents,
        strict=True,
    ):
        require_content_addressed(document, label)
    require_measurement_contract(manifest, measurements, production)
    phase = _phase(values)
    if phase == "pre-0010":
        predecessor = load_json(Path(_string(values, "predecessor-receipt")))
        import_paths = values.get("evidence-import", ())
        hash_paths = values.get("evidence-hash", ())
        if not isinstance(import_paths, tuple) or not isinstance(hash_paths, tuple):
            raise GateHoldError("--evidence-import requires exactly seven imports")
        imports = [load_json(Path(path)) for path in import_paths]
        hashes = [load_json(Path(path)) for path in hash_paths]
        require_pre_0010_join(
            predecessor,
            manifest,
            measurements,
            production,
            documents[3:],
            imports,
            hashes,
            _string(values, "expected-sha"),
            _string(values, "expected-plan-sha256"),
            _string(values, "activation-nonce"),
        )
    if manifest.get("phase") != phase:
        raise GateHoldError("quota manifest phase mismatch")
    _require_fresh(production, now)
    actual = production.get("actual_production_title_body_utf8_bytes")
    amplification = measurements.get("raw_measured_amplification")
    if not isinstance(actual, int) or not isinstance(amplification, (int, float)):
        raise GateHoldError("search projection operands are unknown")
    derived_dimensions = _capture_dimensions(documents[3:], phase, now)
    dimensions = manifest.get("dimensions")
    if not isinstance(dimensions, list):
        raise GateHoldError("manifest dimensions are required")
    derived_values: list[JsonValue] = list(derived_dimensions)
    if canonical_bytes({"dimensions": dimensions}) != canonical_bytes(
        {"dimensions": derived_values}
    ):
        message = "manifest dimension operands do not match provider captures"
        raise GateHoldError(message)
    best_by_dimension: dict[str, JsonObject] = {}
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            raise GateHoldError("dimension must be an object")
        result = _dimension_projection(dimension)
        name = require_string(result.get("name"), "dimension.name")
        ratio = _ratio_value(result)
        prior = best_by_dimension.get(name)
        if prior is None or ratio > _ratio_value(prior):
            best_by_dimension[name] = result
    results: list[JsonValue] = [
        best_by_dimension[name] for name in sorted(best_by_dimension)
    ]
    return with_receipt_sha(
        {
            "schema": "free-tier.result.v1",
            "accepted": True,
            "phase": phase,
            "reviewed_sha": expected_sha,
            "db_now": now.isoformat().replace("+00:00", "Z"),
            "manifest_sha256": sha256_hex(canonical_bytes(manifest)),
            "measurements_sha256": sha256_hex(canonical_bytes(measurements)),
            "production_measurements_sha256": sha256_hex(canonical_bytes(production)),
            "search_projection": search_projection(actual, float(amplification)),
            "dimensions": results,
            **_chain_fields(values),
        }
    )
