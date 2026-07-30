from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast, final
from uuid import UUID, uuid4

import pytest
from app.api.routes.cadence_workflow import (
    CadenceWorkflowAttemptRequest,
    SourceResult,
)
from app.services.release.cadence_workflow_validation import branch_rejection

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

EPOCH = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
FAILED = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
SOURCES = (
    UUID("11111111-1111-4111-8111-111111111111"),
    UUID("22222222-2222-4222-8222-222222222222"),
)
SLOT = "2026-08-01T00:17:00Z"
DUE = datetime(2026, 8, 1, 0, 17, tzinfo=UTC)


@final
class Result:
    def __init__(self, row: Mapping[str, object] | None) -> None:
        self._row = row

    def mappings(self) -> Result:
        return self

    def one_or_none(self) -> Mapping[str, object] | None:
        return self._row


@final
class Session:
    def __init__(
        self,
        row: Mapping[str, object] | None,
        logical: Mapping[str, object] | None = None,
    ) -> None:
        self.row = row
        self.logical = logical

    async def execute(
        self, _statement: object, parameters: object
    ) -> Result:
        if isinstance(parameters, dict) and "cadence_attempt" in parameters:
            return Result(self.logical)
        return Result(self.row)


def request(
    *,
    event: Literal["schedule", "workflow_dispatch"] = "workflow_dispatch",
    mode: Literal["schedule", "retry", "manual"] = "retry",
    attempt: int = 2,
    predecessor: UUID | None = FAILED,
) -> CadenceWorkflowAttemptRequest:
    return CadenceWorkflowAttemptRequest(
        repository="owner/repository",
        workflow="collect.yml",
        head_sha="a" * 40,
        ref="refs/heads/main",
        event=event,
        environment="production-collector",
        run_id=100,
        run_attempt=1,
        epoch_id=EPOCH,
        schedule_kind="collection",
        slot_key=SLOT,
        workflow_mode=mode,
        cadence_attempt=attempt,
        failed_predecessor_attempt_id=predecessor,
        started_at=DUE + timedelta(minutes=2),
        completed_at=DUE + timedelta(minutes=3),
        source_results=tuple(
            SourceResult(
                source_id=source,
                succeeded=True,
                receipt_sha256="d" * 64,
            )
            for source in SOURCES
        ),
    )


@pytest.mark.asyncio
async def test_timely_manual_attempt_two_requires_exact_failed_slot_proof() -> None:
    row = {
        "accepted": False,
        "retry_permitted": True,
        "cadence_epoch_id": EPOCH,
        "schedule_kind": "collection",
        "slot_key": SLOT,
    }
    session = cast("AsyncSession", cast("object", Session(row)))
    assert await branch_rejection(session, request()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "row",
    [
        None,
        {
            "accepted": True,
            "retry_permitted": False,
            "cadence_epoch_id": EPOCH,
            "schedule_kind": "collection",
            "slot_key": SLOT,
        },
        {
            "accepted": False,
            "retry_permitted": True,
            "cadence_epoch_id": uuid4(),
            "schedule_kind": "collection",
            "slot_key": SLOT,
        },
    ],
)
async def test_retry_without_usable_exact_predecessor_is_rejected(
    row: Mapping[str, object] | None,
) -> None:
    session = cast("AsyncSession", cast("object", Session(row)))
    assert await branch_rejection(session, request()) == "retry_proof_invalid"


@pytest.mark.asyncio
async def test_initial_and_manual_branch_identities_are_distinct() -> None:
    session = cast("AsyncSession", cast("object", Session(None)))
    assert (
        await branch_rejection(
            session,
            request(event="schedule", mode="schedule", attempt=1, predecessor=None),
        )
        is None
    )
    manual = request(mode="manual", attempt=1, predecessor=None)
    assert manual.workflow_mode == "manual"


@pytest.mark.asyncio
async def test_second_run_cannot_replace_a_recorded_logical_attempt() -> None:
    predecessor = {
        "accepted": False,
        "retry_permitted": True,
        "cadence_epoch_id": EPOCH,
        "schedule_kind": "collection",
        "slot_key": SLOT,
    }
    logical = {"attempt_id": uuid4()}
    retry_session = cast(
        "AsyncSession", cast("object", Session(predecessor, logical))
    )
    assert (
        await branch_rejection(retry_session, request())
        == "retry_attempt_already_recorded"
    )
    initial_session = cast(
        "AsyncSession", cast("object", Session(None, logical))
    )
    initial = request(
        event="schedule", mode="schedule", attempt=1, predecessor=None
    )
    assert (
        await branch_rejection(initial_session, initial)
        == "initial_attempt_already_recorded"
    )
