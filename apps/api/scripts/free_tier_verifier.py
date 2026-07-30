"""Database-time free-tier result verification."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import os
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

type Args = dict[str, str | tuple[str, ...] | bool]
MAX_CAPTURE_AGE = timedelta(hours=2)
CAPTURE_MAX_AGE_SECONDS = 7_200
INSTRUMENTED_HTTP_CALLS = 2
PAGE_REQUEST_EQUIVALENT = 10_000
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


def verify_command(values: Args) -> JsonObject:  # noqa: C901
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
