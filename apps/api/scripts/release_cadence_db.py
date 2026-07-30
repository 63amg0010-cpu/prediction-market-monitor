"""Transaction-owning PostgreSQL adapter for durable cadence evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, final

from apps.api.scripts import release_cadence_sql as sql
from apps.api.scripts.release_cadence_db_validation import (
    aware_datetime,
    insert_or_verify_contract,
    lock_and_verify,
    raise_cadence,
    slot_row,
    source_identities,
    verify_contract,
)
from apps.api.scripts.release_cadence_models import (
    AttemptOutcome,
    CadenceAttempt,
    CadenceEpoch,
    CadenceSlot,
    ScheduleKind,
)
from apps.api.scripts.release_cadence_slots import (
    EXPECTED_COUNTS,
    attempt_rejection,
    retry_allowed,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine


@final
class PostgresCadenceStore:
    """Run each materialization or CAS attempt in one database transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        """Bind a transaction-owning asynchronous engine."""
        self._engine = engine

    async def materialize(
        self,
        epoch: CadenceEpoch,
        slots: Sequence[CadenceSlot],
    ) -> None:
        """Verify latest activation state and insert exactly 3,120 slots."""
        async with self._engine.begin() as connection:
            await lock_and_verify(connection, epoch)
            identities = await source_identities(connection, epoch)
            await insert_or_verify_contract(connection, epoch, identities)
            parameters = [
                {
                    "epoch_id": epoch.epoch_id,
                    "schedule_kind": item.schedule_kind,
                    "slot_key": item.slot_key,
                    "due_at": item.due_at,
                }
                for item in slots
            ]
            _ = await connection.execute(sql.INSERT_SLOT, parameters)
            rows = (
                await connection.execute(
                    sql.COUNT_SLOTS, {"epoch_id": epoch.epoch_id}
                )
            ).mappings()
            counts = {
                str(typed["schedule_kind"]): int(str(typed["slot_count"]))
                for row in rows
                if (typed := cast("Mapping[str, object]", row))
            }
            if counts != EXPECTED_COUNTS:
                raise_cadence("slot_cardinality_invalid")

    async def record_attempt(
        self,
        epoch: CadenceEpoch,
        attempt: CadenceAttempt,
    ) -> AttemptOutcome:
        """Retain one attempt and atomically CAS the exact workflow slot."""
        async with self._engine.begin() as connection:
            await lock_and_verify(connection, epoch)
            await verify_contract(connection, epoch)
            row = await slot_row(connection, attempt)
            kind = cast("ScheduleKind", row["schedule_kind"])
            slot = CadenceSlot(
                epoch_id=epoch.epoch_id,
                schedule_kind=kind,
                slot_key=cast("str", row["slot_key"]),
                due_at=aware_datetime(row, "due_at"),
            )
            rejection = attempt_rejection(epoch, slot, attempt)
            if row["accepted_attempt_id"] is not None:
                rejection = "duplicate_after_acceptance"
            retry = (
                retry_allowed(slot, attempt, rejection)
                if rejection is not None
                else False
            )
            inserted = await connection.execute(
                sql.INSERT_ATTEMPT,
                _attempt_parameters(attempt, rejection, retry),
            )
            if inserted.rowcount != 1:
                return AttemptOutcome(
                    attempt_id=attempt.attempt_id,
                    accepted=False,
                    reason="duplicate_attempt",
                    retry_permitted=False,
                )
            for receipt in attempt.source_subreceipts:
                _ = await connection.execute(
                    sql.INSERT_SUBRECEIPT,
                    {
                        "attempt_id": attempt.attempt_id,
                        "source_id": receipt.source_id,
                        "succeeded": receipt.succeeded,
                        "receipt_sha": receipt.receipt_sha256,
                    },
                )
            accepted = False
            if rejection is None:
                cas = await connection.execute(
                    sql.CAS_SLOT,
                    {
                        "attempt_id": attempt.attempt_id,
                        "epoch_id": epoch.epoch_id,
                        "schedule_kind": attempt.schedule_kind,
                        "slot_key": attempt.slot_key,
                    },
                )
                accepted = cas.rowcount == 1
                rejection = "accepted" if accepted else "duplicate_after_acceptance"
            retry = retry if not accepted else False
            _ = await connection.execute(
                sql.FINALIZE_ATTEMPT,
                {
                    "accepted": accepted,
                    "attempt_id": attempt.attempt_id,
                    "reason": rejection,
                    "retry_permitted": retry,
                },
            )
            return AttemptOutcome(
                attempt_id=attempt.attempt_id,
                accepted=accepted,
                reason=rejection,
                retry_permitted=retry,
            )


def _attempt_parameters(
    attempt: CadenceAttempt,
    rejection: str | None,
    retry: bool,
) -> dict[str, object]:
    return {
        "attempt_id": attempt.attempt_id,
        "binding_sha": attempt.binding_sha256,
        "completed_at": attempt.completed_at,
        "eligible": rejection is None,
        "epoch_id": attempt.epoch_id,
        "epoch_sha": attempt.epoch_sha256,
        "reason": rejection or "eligible",
        "retry_permitted": retry,
        "schedule_kind": attempt.schedule_kind,
        "scope_sha": attempt.scope_sha256,
        "slot_key": attempt.slot_key,
        "started_at": attempt.started_at,
        "workflow_mode": attempt.mode,
        "workflow_file": attempt.workflow_file,
        "workflow_run_id": attempt.workflow_run_id,
        "workflow_run_attempt": attempt.workflow_run_attempt,
        "cadence_attempt": attempt.cadence_attempt,
        "failed_predecessor_attempt_id": attempt.failed_predecessor_attempt_id,
    }


__all__ = ("PostgresCadenceStore",)
