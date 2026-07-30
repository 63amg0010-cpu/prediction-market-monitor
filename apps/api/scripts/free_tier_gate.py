# pyright: reportUnnecessaryComparison=false
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "anyio>=4.8,<5", "httpx2[brotli,http2,zstd]>=2.5,<3",
#   "orjson>=3.10,<4", "pydantic>=2.10,<3",
#   "sqlalchemy[asyncio]>=2.0.36,<3",
# ]
# ///
# ─── How to run ───
# uv run --package monitor-api python apps/api/scripts/free_tier_gate.py --help
"""Run the schema-closed free-tier evidence CLI."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import assert_never

import anyio
import httpx2
import orjson
from sqlalchemy.exc import SQLAlchemyError

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from apps.api.scripts.free_tier_captures import (
    import_provider_capture,
    validate_verified_capture,
)
from apps.api.scripts.free_tier_db import (
    local_measurement,
    production_measurement,
    provenance,
)
from apps.api.scripts.free_tier_db import (
    production_statements as _production_statements,
)
from apps.api.scripts.free_tier_domain import (
    ARTIFACT_RETENTION_HOURS,
    PHASES,
    PROVIDERS,
    STRICT_THRESHOLD,
    GateHoldError,
    JsonObject,
    canonical_bytes,
    dimension_result,
    search_projection,
    sha256_hex,
    with_receipt_sha,
    write_json,
)
from apps.api.scripts.free_tier_domain import (
    fixture_rows as _fixture_rows,
)
from apps.api.scripts.free_tier_domain import (
    page_bound_for_window as _page_bound_for_window,
)
from apps.api.scripts.free_tier_verifier import verify_command

type Args = dict[str, str | tuple[str, ...] | bool]
fixture_rows = _fixture_rows
page_bound_for_window = _page_bound_for_window
production_statements = _production_statements
__all__ = [
    "GateHoldError",
    "JsonObject",
    "canonical_bytes",
    "dimension_result",
    "fixture_rows",
    "import_provider_capture",
    "main",
    "page_bound_for_window",
    "production_statements",
    "search_projection",
    "sha256_hex",
    "validate_verified_capture",
    "with_receipt_sha",
]


class FreeTierCommand(StrEnum):
    CAPTURE_TEMPLATE = "capture-template"
    IMPORT_PROVIDER_CAPTURE = "import-provider-capture"
    MEASURE_LOCAL = "measure-local"
    MEASURE_PRODUCTION = "measure-production"
    VERIFY = "verify"


def _arguments(argv: list[str]) -> tuple[FreeTierCommand, Args]:
    if not argv:
        raise GateHoldError("a subcommand is required")
    values: Args = {}
    repeated = {"identity-env", "provider-capture"}
    index = 1
    while index < len(argv):
        flag = argv[index]
        if not flag.startswith("--"):
            raise GateHoldError(f"invalid argument: {flag}")
        name = flag[2:]
        if name == "read-only":
            values[name] = True
            index += 1
            continue
        if index + 1 >= len(argv):
            raise GateHoldError(f"value required for {flag}")
        value = argv[index + 1]
        if name in repeated:
            prior = values.get(name, ())
            if not isinstance(prior, tuple):
                raise GateHoldError(f"invalid repeated argument: {flag}")
            values[name] = (*prior, value)
        elif name in values:
            raise GateHoldError(f"duplicate argument: {flag}")
        else:
            values[name] = value
        index += 2
    try:
        command = FreeTierCommand(argv[0])
    except ValueError as error:
        raise GateHoldError(f"unsupported subcommand: {argv[0]}") from error
    return command, values


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


def _database_url(values: Args) -> str:
    name = _string(values, "database-url-env")
    value = os.environ.get(name)
    if value is None or not value:
        raise GateHoldError(f"database environment is empty: {name}")
    return value


def _chain_fields(values: Args) -> JsonObject:
    fields: JsonObject = {}
    for name in ("expected-plan-sha256", "activation-nonce", "predecessor-receipt"):
        value = _optional(values, name)
        if value is not None:
            fields[name.replace("-", "_")] = value
    return fields


def _template(values: Args) -> JsonObject:
    return with_receipt_sha(
        {
            "schema": "free-tier.quota-manifest.v1",
            "phase": _phase(values),
            "reviewed_sha": _string(values, "expected-sha"),
            "required_providers": list(PROVIDERS),
            "artifact_retention_hours": ARTIFACT_RETENTION_HOURS,
            "threshold_exclusive": STRICT_THRESHOLD,
            "capture_max_age_seconds_exclusive": 7_200,
            **_chain_fields(values),
        }
    )


def main(argv: list[str] | None = None) -> int:
    """Run one free-tier evidence subcommand."""
    command, values = _arguments(sys.argv[1:] if argv is None else argv)
    expected_sha = _string(values, "expected-sha")
    match command:
        case FreeTierCommand.CAPTURE_TEMPLATE:
            receipt = _template(values)
        case FreeTierCommand.IMPORT_PROVIDER_CAPTURE:
            identities = values.get("identity-env", ())
            if not isinstance(identities, tuple):
                raise GateHoldError("--identity-env must be repeated")
            receipt = import_provider_capture(
                provider=_string(values, "provider"),
                input_path=Path(_string(values, "input")),
                identity_envs=identities,
                expected_sha=expected_sha,
                phase=_phase(values),
            )
            body = {
                key: value for key, value in receipt.items() if key != "receipt_sha256"
            }
            receipt = with_receipt_sha({**body, **_chain_fields(values)})
        case FreeTierCommand.MEASURE_LOCAL:
            manifest = Path(_string(values, "command-manifest"))
            raw = anyio.run(
                local_measurement,
                _database_url(values),
                _string(values, "api-url"),
                _string(values, "web-url"),
            )
            receipt = with_receipt_sha(
                {
                    "schema": "free-tier.local-measurement.v1",
                    "reviewed_sha": expected_sha,
                    **raw,
                    "provenance": provenance(expected_sha, manifest),
                }
            )
        case FreeTierCommand.MEASURE_PRODUCTION:
            if values.get("read-only") is not True:
                raise GateHoldError("measure-production requires --read-only")
            raw = anyio.run(
                production_measurement,
                _database_url(values),
                _string(values, "expected-current"),
            )
            receipt = with_receipt_sha(
                {
                    "schema": "free-tier.production-measurement.v1",
                    "phase": _phase(values),
                    "reviewed_sha": expected_sha,
                    "project_target": _string(values, "project-target"),
                    "transaction_read_only": True,
                    "sampled": False,
                    **raw,
                    "provenance": provenance(expected_sha),
                    **_chain_fields(values),
                }
            )
        case FreeTierCommand.VERIFY:
            receipt = verify_command(values)
        case unreachable:
            assert_never(unreachable)
    write_json(Path(_string(values, "json-out")), receipt)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        GateHoldError,
        OSError,
        TypeError,
        ValueError,
        httpx2.HTTPError,
        orjson.JSONDecodeError,
        SQLAlchemyError,
    ) as error:
        _ = sys.stderr.write(f"free-tier HOLD: {error}\n")
        raise SystemExit(2) from None
