"""Read-only rollback snapshots and Matrix-B/compatibility handler inputs."""

# pyright: reportAny=false, reportArgumentType=false
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, cast
from uuid import UUID

from sqlalchemy import text

from scripts.release_rollback_models import (
    DatabaseRollbackState,
    DeploymentState,
    HealthState,
)
from scripts.release_runtime_database import read_only_repeatable_read

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

    from scripts.release_runtime_http import ReadOnlyHttpProbe

ROLLBACK_SNAPSHOT = text(
    """
    SELECT v.version_num AS revision, s.enabled AS manifold_enabled,
           s.active_authorization_id, s.current_budget_id,
           s.current_binding_id, s.current_cadence_id,
           latest.id AS latest_transition_id,
           latest.state AS latest_transition,
           d.id AS dcinside_id, d.platform AS dcinside_platform,
           d.external_key AS dcinside_external_key,
           d.scope_version AS dcinside_scope_version
    FROM alembic_version v
    JOIN community_sources s ON s.platform = 'manifold'
    JOIN community_sources d ON d.platform = 'dcinside'
    JOIN LATERAL (
        SELECT id, state FROM source_activation_state_transitions
        WHERE activation_nonce = :activation_nonce
        ORDER BY transition_at_db DESC, id DESC LIMIT 1
    ) latest ON true
    """
)


class RollbackRuntimeError(RuntimeError):
    """Stable rollback snapshot error."""


@dataclass(frozen=True, slots=True)
class RollbackDatabaseSnapshot:
    """Rollback state and the database time from its exact transaction."""

    state: DatabaseRollbackState
    observed_at: datetime


async def rollback_database_snapshot(
    engine: AsyncEngine,
    activation_nonce: UUID,
) -> RollbackDatabaseSnapshot:
    """Capture rollback DB facts in one read-only repeatable-read snapshot."""

    async def reader(
        connection: AsyncConnection,
        observed_at: datetime,
    ) -> RollbackDatabaseSnapshot:
        row = (
            await connection.execute(
                ROLLBACK_SNAPSHOT,
                {"activation_nonce": activation_nonce},
            )
        ).mappings().one_or_none()
        if row is None:
            msg = "rollback_database_state_missing"
            raise RollbackRuntimeError(msg)
        values = cast("Mapping[str, object]", row)
        dcinside = "\0".join(
            str(values[name])
            for name in (
                "dcinside_id",
                "dcinside_platform",
                "dcinside_external_key",
                "dcinside_scope_version",
            )
        )
        dcinside_sha = sha256(dcinside.encode()).hexdigest()
        transition_id = values["latest_transition_id"]
        if not isinstance(transition_id, UUID):
            msg = "rollback_transition_id_invalid"
            raise RollbackRuntimeError(msg)
        pointers = (
            values["active_authorization_id"],
            values["current_budget_id"],
            values["current_binding_id"],
            values["current_cadence_id"],
        )
        return RollbackDatabaseSnapshot(
            DatabaseRollbackState(
                revision=str(values["revision"]),
                latest_transition=str(values["latest_transition"]),
                latest_transition_id=transition_id.int,
                manifold_enabled=bool(values["manifold_enabled"]),
                active_authorization_id=cast("UUID | None", pointers[0]),
                current_budget_id=cast("UUID | None", pointers[1]),
                current_binding_id=cast("UUID | None", pointers[2]),
                current_cadence_id=cast("UUID | None", pointers[3]),
                original_dcinside_binding_sha256=dcinside_sha,
                current_dcinside_binding_sha256=dcinside_sha,
                zero_provider_binding=(
                    not bool(values["manifold_enabled"])
                    and all(value is None for value in pointers)
                ),
            ),
            observed_at,
        )

    return await read_only_repeatable_read(engine, reader)


async def rollback_database_state(
    engine: AsyncEngine,
    activation_nonce: UUID,
) -> DatabaseRollbackState:
    """Return the state projection while preserving the existing adapter API."""
    return (await rollback_database_snapshot(engine, activation_nonce)).state


def deployment_state(
    receipt: Mapping[str, object],
    *,
    expected_kind: str,
) -> DeploymentState:
    """Project one validated Vercel receipt into rollback state."""
    if receipt.get("accepted") is not True:
        msg = "deployment_receipt_not_accepted"
        raise RollbackRuntimeError(msg)
    if receipt.get("project_kind") != expected_kind:
        msg = "deployment_receipt_kind_mismatch"
        raise RollbackRuntimeError(msg)
    return DeploymentState(
        project_kind=cast("object", expected_kind),  # type: ignore[arg-type]
        project_name=str(receipt.get("project_name", "")),
        team_slug=str(receipt.get("team_slug", "")),
        source_sha=str(receipt.get("source_sha", "")),
        ready_state=str(receipt.get("ready_state", "")),
        environment=str(receipt.get("environment", "")),
        alias=str(receipt.get("alias", "")),
        alias_assigned=True,
        no_op=bool(receipt.get("no_op", False)),
        no_op_verified=bool(receipt.get("no_op_verified", False)),
    )


def health_state(
    api_url: str,
    web_url: str,
    *,
    probe: ReadOnlyHttpProbe,
) -> tuple[HealthState, dict[str, object]]:
    """Corroborate API database health and the live Web surface."""
    try:
        raw = probe.fetch(f"{api_url.rstrip('/')}/health")
        value = cast("object", json.loads(raw))
    except json.JSONDecodeError as error:
        msg = "api_health_json_invalid"
        raise RollbackRuntimeError(msg) from error
    if not isinstance(value, dict):
        msg = "api_health_json_invalid"
        raise RollbackRuntimeError(msg)
    _ = probe.fetch(web_url)
    health = cast("dict[str, object]", value)
    return (
        HealthState(
            api_ok=health.get("status") == "ok",
            web_ok=True,
            dcinside_ok=health.get("dcinside_ok") is True,
            dcinside_search_ok=health.get("dcinside_search_ok") is True,
            manifold_results=int(health.get("manifold_results", -1)),
        ),
        health,
    )


__all__ = (
    "RollbackDatabaseSnapshot",
    "RollbackRuntimeError",
    "deployment_state",
    "health_state",
    "rollback_database_snapshot",
    "rollback_database_state",
)
