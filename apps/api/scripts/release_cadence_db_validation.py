"""Locked database-state validation for the cadence adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Never, cast

from apps.api.scripts import release_cadence_sql as sql
from apps.api.scripts.release_cadence_models import (
    CadenceAttempt,
    CadenceEpoch,
    CadenceError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncConnection


async def lock_and_verify(
    connection: AsyncConnection,
    epoch: CadenceEpoch,
) -> None:
    """Lock an epoch and require its latest activation transition to be active."""
    _ = await connection.execute(sql.LOCK_EPOCH, {"epoch_id": epoch.epoch_id})
    result = await connection.execute(
        sql.CURRENT_EPOCH, {"epoch_id": epoch.epoch_id}
    )
    row = cast("Mapping[str, object] | None", result.mappings().one_or_none())
    if row is None:
        _raise("cadence_epoch_not_current")
    checks = (
        (row["state"] != "active", "latest_transition_not_active"),
        (row["enabled"] is not True, "cadence_source_disabled"),
        (row["current_cadence_id"] != epoch.epoch_id, "source_pointer_mismatch"),
        (
            row["transition_cadence_id"] != epoch.epoch_id,
            "transition_pointer_mismatch",
        ),
        (row["closed_at"] is not None, "cadence_epoch_closed"),
        (
            _datetime(row, "cadence_anchor_at") != epoch.anchor_at,
            "cadence_anchor_mismatch",
        ),
        (
            row["current_binding_sha256"] != epoch.binding_sha256,
            "current_binding_mismatch",
        ),
    )
    failed = next((code for condition, code in checks if condition), None)
    if failed is not None:
        _raise(failed)


async def source_identities(
    connection: AsyncConnection,
    epoch: CadenceEpoch,
) -> dict[str, UUID]:
    """Verify enabled exact dcinside/manifold rows for the frozen set."""
    rows = (
        await connection.execute(
            sql.EXPECTED_SOURCES,
            {
                "source_a": epoch.expected_source_ids[0],
                "source_b": epoch.expected_source_ids[1],
            },
        )
    ).mappings()
    identities: dict[str, UUID] = {}
    for row in rows:
        typed = cast("Mapping[str, object]", row)
        platform = str(typed["platform"])
        if typed["enabled"] is not True or platform not in {"dcinside", "manifold"}:
            _raise("expected_source_state_invalid")
        identities[platform] = cast("UUID", typed["id"])
    if set(identities) != {"dcinside", "manifold"}:
        _raise("expected_source_set_invalid")
    return identities


async def insert_or_verify_contract(
    connection: AsyncConnection,
    epoch: CadenceEpoch,
    identities: Mapping[str, UUID],
) -> None:
    """Insert an immutable epoch contract or byte-compare the retained row."""
    _ = await connection.execute(
        sql.INSERT_CONTRACT,
        {
            "binding_sha": epoch.binding_sha256,
            "closes_at": epoch.closes_at,
            "dcinside_id": identities["dcinside"],
            "epoch_id": epoch.epoch_id,
            "epoch_sha": epoch.epoch_sha256,
            "manifold_id": identities["manifold"],
            "scope_sha": epoch.scope_sha256,
        },
    )
    await verify_contract(connection, epoch)


async def verify_contract(
    connection: AsyncConnection,
    epoch: CadenceEpoch,
) -> None:
    """Reject any retained contract differing from the in-memory epoch."""
    result = await connection.execute(
        sql.SELECT_CONTRACT, {"epoch_id": epoch.epoch_id}
    )
    row = cast("Mapping[str, object] | None", result.mappings().one_or_none())
    if row is None:
        _raise("cadence_contract_missing")
    expected = (
        epoch.epoch_sha256,
        *epoch.expected_source_ids,
        epoch.binding_sha256,
        epoch.scope_sha256,
        epoch.closes_at,
        epoch.invalidated_at,
    )
    actual = (
        row["epoch_sha256"],
        *sorted(
            (row["dcinside_source_id"], row["manifold_source_id"]),
            key=str,
        ),
        row["binding_sha256"],
        row["scope_sha256"],
        _datetime(row, "window_closes_at"),
        row["invalidated_at"],
    )
    if actual != expected:
        _raise("cadence_contract_conflict")


async def slot_row(
    connection: AsyncConnection,
    attempt: CadenceAttempt,
) -> Mapping[str, object]:
    """Lock one exact slot or reject an unmaterialized key."""
    result = await connection.execute(
        sql.SELECT_SLOT,
        {
            "epoch_id": attempt.epoch_id,
            "schedule_kind": attempt.schedule_kind,
            "slot_key": attempt.slot_key,
        },
    )
    row = cast("Mapping[str, object] | None", result.mappings().one_or_none())
    if row is None:
        _raise("slot_not_materialized")
    return row


def _datetime(row: Mapping[str, object], key: str) -> datetime:
    value = row[key]
    if not isinstance(value, datetime) or value.tzinfo is None:
        _raise(f"{key}_invalid")
    return value.astimezone(UTC)


def _raise(code: str) -> Never:
    raise CadenceError(code)


def aware_datetime(row: Mapping[str, object], key: str) -> datetime:
    """Expose strict timestamp parsing to the transaction adapter."""
    return _datetime(row, key)


def raise_cadence(code: str) -> Never:
    """Raise one stable fail-closed cadence error."""
    _raise(code)


__all__ = (
    "aware_datetime",
    "insert_or_verify_contract",
    "lock_and_verify",
    "raise_cadence",
    "slot_row",
    "source_identities",
    "verify_contract",
)
