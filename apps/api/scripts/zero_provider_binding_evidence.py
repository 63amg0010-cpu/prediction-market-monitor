"""Create redacted binding-control evidence without provider access."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Final, NoReturn, final
from uuid import UUID

from pydantic import JsonValue, TypeAdapter

MODES: Final = frozenset(
    {"binding-prestate", "binding-handshake", "binding-restore-verify"}
)
BINDINGS: Final = TypeAdapter(list[dict[str, JsonValue]])


@final
class Args(argparse.Namespace):
    """Typed parsed arguments for the zero-provider command."""

    mode: str = ""
    activation_nonce: str = ""
    source_ids: str = ""
    scope_version: str = ""
    bindings_json_env: str = ""
    json_out: str = ""


class EvidenceError(RuntimeError):
    """Public-safe zero-provider evidence failure."""


def reject(code: str) -> NoReturn:
    """Fail with a stable public-safe code."""
    raise EvidenceError(code)


def canonical(value: object) -> bytes:
    """Serialize one value into deterministic UTF-8 JSON bytes."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def binding_platform(binding: dict[str, JsonValue]) -> str:
    """Resolve a binding platform without exposing authorization content."""
    platform = binding.get("platform")
    if not isinstance(platform, str) or not platform:
        authorization = binding.get("authorization")
        if isinstance(authorization, dict):
            platform = authorization.get("source")
    if not isinstance(platform, str) or not platform:
        reject("binding_platform_missing")
    return platform


def parser() -> argparse.ArgumentParser:
    """Build the closed zero-provider CLI parser."""
    result = argparse.ArgumentParser()
    _ = result.add_argument("--mode", required=True, choices=sorted(MODES))
    _ = result.add_argument("--activation-nonce", required=True)
    _ = result.add_argument("--source-ids", required=True)
    _ = result.add_argument("--scope-version", required=True)
    _ = result.add_argument("--bindings-json-env", required=True)
    _ = result.add_argument("--json-out", required=True)
    return result


def main() -> int:
    """Validate live binding inputs and emit only redacted evidence."""
    try:
        args = parser().parse_args(namespace=Args())
        _ = UUID(args.activation_nonce)
        raw = os.environ.get(args.bindings_json_env, "")
        if not raw:
            reject("binding_secret_missing")
        decoded = BINDINGS.validate_json(raw)
        if not decoded:
            reject("binding_set_invalid")
        bindings = list(decoded)
        source_ids = args.source_ids.split(",")
        actual_ids = [item.get("source_id") for item in bindings]
        if actual_ids != source_ids or len(source_ids) != len(set(source_ids)):
            reject("binding_source_ids_mismatch")
        for source_id in source_ids:
            _ = UUID(source_id)
        platforms = [binding_platform(item) for item in bindings]
        protected_json = canonical(bindings).decode()
        payload = {
            "protected_json": protected_json,
            "scope_version": args.scope_version,
            "source_ids": args.source_ids,
        }
        receipt = {
            "accepted": True,
            "activation_nonce": args.activation_nonce,
            "mode": args.mode,
            "payload_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
            "platforms": platforms,
            "provider_request_count": 0,
            "raw_binding_persisted": False,
            "scope_version": args.scope_version,
            "source_ids": args.source_ids,
        }
        output = Path(args.json_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        _ = output.write_bytes(canonical(receipt) + b"\n")
    except (
        EvidenceError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        _ = sys.stderr.write(f"zero-provider binding evidence HOLD: {error}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
