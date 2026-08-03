"""Executable facade for the eight source-binding commands."""

# pyright: reportUnnecessaryComparison=false
# ruff: noqa: D103, EM101, TC002, TC003, TRY003

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import assert_never
from uuid import UUID

import anyio
from pydantic import JsonValue
from sqlalchemy import text

from scripts.source_bindings_contracts import (
    BINDINGS,
    DOCUMENT,
    MUTATING,
    Args,
    BindingConflictError,
    BindingPayload,
    CliError,
    GitHub,
    GitHubCommand,
    JsonDocument,
    binding_payload,
    canonical,
    field,
    load,
    parser,
    platforms,
    required,
    sha,
    write,
)
from scripts.source_bindings_db import locked as _locked
from scripts.source_bindings_github import SubprocessGitHub, get_variable
from scripts.source_bindings_mutations import mutate

SHA256_LENGTH = 64
REQUIRED_COLLECTION_SOURCES = 2


def capture(args: Args, github: GitHub) -> JsonDocument:
    protected_path = required(args.protected_json_file, "--protected-json-file")
    protected = Path(protected_path).read_text(encoding="utf-8")
    bindings = BINDINGS.validate_json(protected)
    protected = canonical(list(bindings)).decode()
    source_ids = get_variable(github, "MONITOR_SOURCE_IDS")
    scope = get_variable(github, "MONITOR_SCOPE_VERSION")
    actual_platforms = [field(binding, "platform") for binding in bindings]
    if actual_platforms != platforms(args):
        raise CliError("platform set does not match captured bindings")
    expected_ids = ",".join(field(binding, "source_id") for binding in bindings)
    if source_ids != expected_ids:
        raise CliError("source IDs do not match captured bindings")
    payload = BindingPayload(protected, source_ids, scope)
    evidence = load(required(args.collection_receipt, "--collection-receipt"))
    expected_evidence: JsonDocument = {
        "accepted": True,
        "activation_nonce": args.activation_nonce,
        "mode": "binding-prestate",
        "payload_sha256": payload.sha256,
        "provider_request_count": 0,
        "scope_version": scope,
        "source_ids": source_ids,
    }
    if any(evidence.get(key) != value for key, value in expected_evidence.items()):
        raise CliError("binding prestate evidence does not match live binding")
    platform_values: list[JsonValue] = list(actual_platforms)
    receipt: JsonDocument = {
        "activation_nonce": args.activation_nonce,
        "command": "capture-prestate",
        "payload_sha256": payload.sha256,
        "platforms": platform_values,
        "protected_json": protected,
        "scope_version": scope,
        "source_ids": source_ids,
    }
    receipt["prestate_sha256"] = sha(receipt)
    return receipt


def render(args: Args) -> JsonDocument:
    predecessor = load(required(args.predecessor_receipt, "--predecessor-receipt"))
    if field(predecessor, "activation_nonce") != args.activation_nonce:
        raise CliError("predecessor activation nonce mismatch")
    prior = BINDINGS.validate_json(field(predecessor, "protected_json"))
    addition = DOCUMENT.validate_json(
        Path(required(args.binding_file, "--binding-file")).read_bytes()
    )
    bindings = [*prior, addition]
    actual_platforms = [field(binding, "platform") for binding in bindings]
    if actual_platforms != platforms(args):
        raise CliError("platform set does not match rendered bindings")
    binding_values: list[JsonValue] = list(bindings)
    payload = BindingPayload(
        protected_json=canonical(binding_values).decode(),
        source_ids=",".join(field(binding, "source_id") for binding in bindings),
        scope_version=f"{field(predecessor, 'scope_version')}+manifold-v1",
    )
    platform_values: list[JsonValue] = list(actual_platforms)
    return {
        "activation_nonce": args.activation_nonce,
        "command": "render",
        "payload_sha256": payload.sha256,
        "platforms": platform_values,
        "predecessor_sha256": sha(predecessor),
        "protected_json": payload.protected_json,
        "scope_version": payload.scope_version,
        "source_ids": payload.source_ids,
    }


def validate(args: Args) -> JsonDocument:
    payload_doc = load(required(args.payload_receipt, "--payload-receipt"))
    predecessor = load(required(args.predecessor_receipt, "--predecessor-receipt"))
    payload = binding_payload(payload_doc, UUID(args.activation_nonce))
    if payload_doc.get("predecessor_sha256") != sha(predecessor):
        raise CliError("predecessor hash does not match payload")
    bindings = BINDINGS.validate_json(payload.protected_json)
    actual_platforms = [field(binding, "platform") for binding in bindings]
    if actual_platforms != platforms(args):
        raise CliError("platform set does not match payload")
    platform_values: list[JsonValue] = list(actual_platforms)
    return {
        "activation_nonce": args.activation_nonce,
        "command": "validate",
        "payload_sha256": payload.sha256,
        "platforms": platform_values,
        "predecessor_sha256": sha(predecessor),
        "valid": True,
    }


async def verify(args: Args, github: GitHub) -> JsonDocument:
    nonce = UUID(args.activation_nonce)
    payload = binding_payload(
        load(required(args.payload_receipt, "--payload-receipt")),
        nonce,
    )
    collection = load(required(args.collection_receipt, "--collection-receipt"))
    validate_collection_receipt(collection, payload)
    if get_variable(github, "MONITOR_SOURCE_IDS") != payload.source_ids:
        raise CliError("live source IDs do not match")
    if get_variable(github, "MONITOR_SCOPE_VERSION") != payload.scope_version:
        raise CliError("live scope marker does not match")
    env_name = required(args.database_url_env, "--database-url-env")
    database_url = os.environ.get(env_name)
    if not database_url:
        raise CliError("MIGRATION_DATABASE_URL environment is required")
    async with _locked(database_url) as connection:
        state = (
            await connection.execute(
                text(
                    """
                    SELECT state FROM source_activation_state_transitions
                    WHERE activation_nonce = :nonce
                    ORDER BY transition_at_db DESC, id DESC LIMIT 1
                    """
                ),
                {"nonce": nonce},
            )
        ).scalar_one_or_none()
        if state != "active":
            raise CliError("binding intent is not active")
    return {
        "activation_nonce": str(nonce),
        "collection_receipt_sha256": sha(collection),
        "command": "verify-github",
        "payload_sha256": payload.sha256,
        "verified": True,
    }


def validate_collection_receipt(
    collection: JsonDocument,
    payload: BindingPayload,
) -> None:
    """Require the real successful two-source collector result artifact."""
    if collection.get("schema") != "cadence-operation-result.v1":
        raise CliError("collection receipt schema mismatch")
    if collection.get("schedule_kind") != "collection":
        raise CliError("collection receipt is not a collection result")
    results = collection.get("source_results")
    if not isinstance(results, list):
        raise CliError("collection receipt source results missing")
    expected_ids = payload.source_ids.split(",")
    actual_ids: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            raise CliError("collection receipt source result invalid")
        source_id = result.get("source_id")
        if not isinstance(source_id, str):
            raise CliError("collection receipt source ID invalid")
        if (
            result.get("status") != "succeeded"
            or result.get("code") != "ok"
            or result.get("retry_classification") != "not_applicable"
        ):
            raise CliError("collection receipt has unsuccessful source")
        receipt_sha = result.get("receipt_sha256")
        if (
            not isinstance(receipt_sha, str)
            or len(receipt_sha) != SHA256_LENGTH
            or any(char not in "0123456789abcdef" for char in receipt_sha)
        ):
            raise CliError("collection receipt source hash invalid")
        actual_ids.append(source_id)
    if actual_ids != expected_ids or len(results) != REQUIRED_COLLECTION_SOURCES:
        raise CliError("collection receipt source set mismatch")


def run(argv: Sequence[str] | None = None, github: GitHub | None = None) -> int:
    args = parser().parse_args(argv, namespace=Args())
    client = github or SubprocessGitHub()
    if args.command in MUTATING and (
        not args.database_url_env or not os.environ.get(args.database_url_env)
    ):
        raise CliError("MIGRATION_DATABASE_URL environment is required")
    match args.command:
        case "capture-prestate":
            receipt = capture(args, client)
        case "render":
            receipt = render(args)
        case "validate":
            receipt = validate(args)
        case (
            "apply-github"
            | "handshake-github"
            | "finalize-github"
            | "restore-github"
        ):
            receipt = anyio.run(mutate, args, client)
        case "verify-github":
            receipt = anyio.run(verify, args, client)
        case unreachable:
            assert_never(unreachable)
    write(args, receipt)
    return 0


def main() -> int:
    try:
        return run()
    except (
        BindingConflictError,
        CliError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        _ = sys.stderr.write(f"{error}\n")
        return 2


__all__ = (
    "BindingConflictError",
    "GitHubCommand",
    "SubprocessGitHub",
    "_locked",
    "main",
    "run",
)
