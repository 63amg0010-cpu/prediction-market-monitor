"""Runtime boundary for read-only Vercel compatibility validation."""

# ruff: noqa: EM101
# pyright: reportAny=false, reportArgumentType=false
# pyright: reportAttributeAccessIssue=false
# pyright: reportGeneralTypeIssues=false, reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from scripts.release_gate_cli_io import read_document, write_document
from scripts.release_runtime_database import engine_from_named_env
from scripts.release_runtime_http import ReadOnlyHttpProbe
from scripts.release_runtime_retention import (
    AliasRetentionProvider,
    AliasRetentionRuntime,
)
from scripts.release_runtime_rollback import (
    deployment_state,
    health_state,
    rollback_database_snapshot,
)
from scripts.release_vercel_compat import (
    CompatibilityDatabaseState,
    CompatibilityStateInput,
    validate_compat_state,
)
from scripts.release_vercel_models import ReleaseHoldError, verify_receipt
from scripts.release_vercel_retention import (
    build_alias_retention_proof,
    parse_utc_timestamp,
)

if TYPE_CHECKING:
    import argparse
    from collections.abc import Awaitable, Callable


def _run_in_local_selector(
    coroutine_factory: Callable[[], Awaitable[object]],
) -> object:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        return runner.run(coroutine_factory())


def _load_states(
    args: argparse.Namespace,
    cadence_anchor_at: datetime,
) -> object:
    engine = engine_from_named_env(args.database_url_env)

    async def load() -> object:
        try:
            return await rollback_database_snapshot(
                engine, UUID(args.activation_nonce)
            )
        finally:
            await engine.dispose()

    snapshot = _run_in_local_selector(load)
    if snapshot.observed_at >= cadence_anchor_at + timedelta(days=31):
        raise ReleaseHoldError("alias_retention_expired")
    return snapshot


def _compat_anchor(
    args: argparse.Namespace,
    now: Callable[[], datetime] | None,
) -> datetime:
    raw = getattr(args, "cadence_anchor_at", None)
    if raw is None or raw == "":
        raise ReleaseHoldError("cadence_anchor_at_missing")
    if not isinstance(raw, str):
        raise ReleaseHoldError("cadence_anchor_at_invalid")
    anchor = parse_utc_timestamp(raw, "cadence_anchor_at")
    current = datetime.now(UTC) if now is None else now()
    if current.tzinfo is None or current.utcoffset() != timedelta(0):
        raise ReleaseHoldError("runtime_now_must_be_utc_aware")
    if current >= anchor + timedelta(days=31):
        raise ReleaseHoldError("alias_retention_expired")
    return anchor


def compat_state(
    args: argparse.Namespace,
    *,
    now: Callable[[], datetime] | None = None,
    retention_runtime: AliasRetentionProvider | None = None,
) -> int:
    """Validate live 0010 compatibility and exact retained aliases."""
    cadence_anchor_at = _compat_anchor(args, now)
    snapshot = _load_states(args, cadence_anchor_at)
    database = snapshot.state
    db_now = snapshot.observed_at
    alias_api = read_document(args.api_alias_receipt)
    alias_web = read_document(args.web_alias_receipt)
    for receipt in (
        read_document(args.api_receipt),
        read_document(args.web_receipt),
        alias_api,
        alias_web,
    ):
        _ = verify_receipt(
            receipt,
            expected_sha=args.expected_sha,
            expected_plan_sha256=args.expected_plan_sha256,
            activation_nonce=UUID(args.activation_nonce),
        )
    runtime = (
        AliasRetentionRuntime()
        if retention_runtime is None
        else retention_runtime
    )
    api_observation = runtime.observe("api", alias_api, db_now)
    web_observation = runtime.observe("web", alias_web, db_now)
    health, raw_health = health_state(
        args.api_url,
        args.web_url,
        probe=ReadOnlyHttpProbe(),
    )
    request = CompatibilityStateInput(
        database=CompatibilityDatabaseState(
            revision=database.revision,
            manifold_rows=int(raw_health.get("manifold_rows", -1)),
            manifold_enabled=database.manifold_enabled,
            active_pointer_count=sum(
                value is not None
                for value in (
                    database.active_authorization_id,
                    database.current_budget_id,
                    database.current_binding_id,
                    database.current_cadence_id,
                )
            ),
        ),
        api=deployment_state(alias_api, expected_kind="api"),
        web=deployment_state(alias_web, expected_kind="web"),
        health=health,
        api_claim_endpoint_compatible=(
            raw_health.get("claim_endpoint_compatible") is True
        ),
        api_evidence_endpoint_compatible=(
            raw_health.get("evidence_endpoint_compatible") is True
        ),
        api_receipt=alias_api,
        web_receipt=alias_web,
        expected_sha=args.expected_sha,
        expected_plan_sha256=args.expected_plan_sha256,
        activation_nonce=UUID(args.activation_nonce),
        predecessor_receipt=read_document(args.predecessor_receipt),
        cadence_anchor_at=cadence_anchor_at,
        db_now=db_now,
        api_retention=build_alias_retention_proof(
            observation=api_observation,
            alias_receipt=alias_api,
            cadence_anchor_at=cadence_anchor_at,
        ),
        web_retention=build_alias_retention_proof(
            observation=web_observation,
            alias_receipt=alias_web,
            cadence_anchor_at=cadence_anchor_at,
        ),
    )
    write_document(args.json_out, validate_compat_state(request))
    return 0


__all__ = ("compat_state",)
