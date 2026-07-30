"""Locked GitHub mutation commands for source bindings."""

# pyright: reportUnnecessaryComparison=false
# ruff: noqa: D103, EM101, TRY003

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never
from uuid import UUID

from scripts.source_bindings_contracts import (
    MUTATION_COMMAND,
    REPOSITORY,
    Args,
    BindingPayload,
    CliError,
    GitHub,
    GitHubCommand,
    JsonDocument,
    binding_payload,
    load,
    required,
    sha,
)
from scripts.source_bindings_db import (
    append_transition,
    ensure_intent,
    latest_state,
    locked,
)
from scripts.source_bindings_github import (
    get_variable,
    set_secret,
    set_variable,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class MutationContext:
    args: Args
    github: GitHub
    connection: AsyncConnection
    nonce: UUID
    payload: BindingPayload
    prestate: JsonDocument
    intent_id: UUID
    attestation_id: UUID
    latest_state: str | None


async def _apply(context: MutationContext) -> tuple[str, bool]:
    if context.latest_state not in {
        "prepared",
        "binding_writing",
        "binding_committed",
    }:
        raise CliError("apply predecessor state is invalid")
    await append_transition(
        context.connection,
        context.nonce,
        context.intent_id,
        context.attestation_id,
        "binding_writing",
    )
    recovered = (
        get_variable(context.github, "MONITOR_SCOPE_VERSION")
        == context.payload.scope_version
        and get_variable(context.github, "MONITOR_SOURCE_IDS")
        == context.payload.source_ids
    )
    if not recovered:
        set_secret(context.github, context.payload.protected_json)
        set_variable(
            context.github,
            "MONITOR_SOURCE_IDS",
            context.payload.source_ids,
        )
        set_variable(
            context.github,
            "MONITOR_SCOPE_VERSION",
            context.payload.scope_version,
        )
    await append_transition(
        context.connection,
        context.nonce,
        context.intent_id,
        context.attestation_id,
        "binding_committed",
    )
    return "binding_committed", recovered


async def _handshake(context: MutationContext) -> tuple[str, bool]:
    if context.latest_state not in {"binding_committed", "handshake_passed"}:
        raise CliError("handshake predecessor state is invalid")
    if context.args.handshake_receipt:
        receipt = load(context.args.handshake_receipt)
        expected: JsonDocument = {
            "accepted": True,
            "activation_nonce": str(context.nonce),
            "mode": "binding-handshake",
            "payload_sha256": context.payload.sha256,
            "scope_version": context.payload.scope_version,
            "source_ids": context.payload.source_ids,
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise CliError("handshake receipt does not match intent")
        await append_transition(
            context.connection,
            context.nonce,
            context.intent_id,
            context.attestation_id,
            "handshake_passed",
        )
        return "handshake_passed", False
    dispatch_nonce = UUID(required(context.args.dispatch_nonce, "--dispatch-nonce"))
    attempt = context.args.attempt
    if attempt not in {1, 2}:
        raise CliError("--attempt must be 1 or 2")
    _ = context.github.execute(
        GitHubCommand(
            (
                "gh",
                "workflow",
                "run",
                "collector.yml",
                "--repo",
                REPOSITORY,
                "--ref",
                "main",
                "-f",
                "mode=binding-handshake",
                "-f",
                f"activation_nonce={context.nonce}",
                "-f",
                f"payload_sha256={context.payload.sha256}",
                "-f",
                f"dispatch_nonce={dispatch_nonce}",
                "-f",
                f"attempt={attempt}",
            )
        )
    )
    return "handshake_dispatched", False


async def _finalize(context: MutationContext) -> tuple[str, bool]:
    if context.latest_state not in {"anchor_reserved", "github_finalized"}:
        raise CliError("finalize predecessor state is invalid")
    anchor = required(context.args.cadence_anchor_at, "--cadence-anchor-at")
    set_variable(context.github, "MONITOR_DEPLOYMENT_ACTIVATION_AT", anchor)
    if get_variable(context.github, "MONITOR_DEPLOYMENT_ACTIVATION_AT") != anchor:
        raise CliError("activation anchor verification failed")
    await append_transition(
        context.connection,
        context.nonce,
        context.intent_id,
        context.attestation_id,
        "github_finalized",
    )
    return "github_finalized", False


async def _restore(context: MutationContext) -> tuple[str, bool]:
    prestate = binding_payload(context.prestate, context.nonce)
    if context.latest_state == "failed":
        if (
            get_variable(context.github, "MONITOR_SCOPE_VERSION")
            != context.payload.scope_version
        ):
            raise CliError("failed scope marker does not match intent")
        await append_transition(
            context.connection,
            context.nonce,
            context.intent_id,
            context.attestation_id,
            "deactivated",
        )
        await append_transition(
            context.connection,
            context.nonce,
            context.intent_id,
            context.attestation_id,
            "restore_writing",
        )
    elif context.latest_state != "restore_writing":
        raise CliError("restore predecessor state is invalid")
    set_secret(context.github, prestate.protected_json)
    set_variable(context.github, "MONITOR_SOURCE_IDS", prestate.source_ids)
    set_variable(context.github, "MONITOR_SCOPE_VERSION", prestate.scope_version)
    return "restore_writing", False


async def mutate(args: Args, github: GitHub) -> JsonDocument:
    nonce = UUID(args.activation_nonce)
    payload = binding_payload(
        load(required(args.payload_receipt, "--payload-receipt")),
        nonce,
    )
    prestate = load(required(args.prestate_receipt, "--prestate-receipt"))
    attestation_id = UUID(required(args.attestation_id, "--attestation-id"))
    env_name = required(args.database_url_env, "--database-url-env")
    database_url = os.environ.get(env_name)
    if not database_url:
        raise CliError("MIGRATION_DATABASE_URL environment is required")
    async with locked(database_url) as connection:
        intent_id = await ensure_intent(
            connection,
            nonce,
            payload,
            sha(prestate),
            attestation_id,
        )
        context = MutationContext(
            args,
            github,
            connection,
            nonce,
            payload,
            prestate,
            intent_id,
            attestation_id,
            await latest_state(connection, nonce),
        )
        command = MUTATION_COMMAND.validate_python(args.command)
        match command:
            case "apply-github":
                state, recovered = await _apply(context)
            case "handshake-github":
                state, recovered = await _handshake(context)
            case "finalize-github":
                state, recovered = await _finalize(context)
            case "restore-github":
                state, recovered = await _restore(context)
            case unreachable:
                assert_never(unreachable)
    return {
        "activation_nonce": str(nonce),
        "command": args.command,
        "payload_sha256": payload.sha256,
        "recovered_after_lost_receipt": recovered,
        "redacted": True,
        "state": state,
    }


__all__ = ("mutate",)
