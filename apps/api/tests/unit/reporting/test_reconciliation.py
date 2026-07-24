from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import anyio
import pytest
from app.domain.enums import ReportRole, Sentiment
from app.reporting.reconciliation import (
    ReconcileRequest,
    correction_targets,
    reconcile_report,
)
from app.reporting.repository import InMemoryReportRepository

from .factories import manifest_payload, record, valid_analysis

if TYPE_CHECKING:
    from app.reporting.repository_types import AppendReportOutcome

CREATED = datetime(2026, 7, 23, tzinfo=UTC)


def request(seed: int, payload_seed: int) -> ReconcileRequest:
    primary = record(
        payload_seed,
        ReportRole.PRIMARY,
        valid_analysis(
            payload_seed,
            relevance=True,
            sentiment=Sentiment.POSITIVE,
        ),
    )
    return ReconcileRequest(
        payload=manifest_payload((primary,)),
        created_at=CREATED,
        report_id=UUID(int=seed),
        version_id=UUID(int=seed + 100),
        manifest_id=UUID(int=seed + 200),
    )


@pytest.mark.asyncio
async def test_reconciliation_is_idempotent_then_appends_one_correction() -> None:
    # Given: an empty append-only report repository.
    repository = InMemoryReportRepository()

    # When: identical facts reconcile twice and changed P facts reconcile once.
    first = await reconcile_report(repository, request(1, 1))
    duplicate = await reconcile_report(repository, request(2, 1))
    correction = await reconcile_report(repository, request(3, 2))

    # Then: duplicate identity reuses revision one and correction supersedes it.
    assert first.created is True
    assert duplicate.created is False
    assert duplicate.version.version_id == first.version.version_id
    assert correction.created is True
    assert correction.version.revision == 2
    assert correction.version.supersedes_version_id == first.version.version_id
    assert first.version.retain_until == CREATED + timedelta(days=180)
    assert len(repository.history(first.version.report_date_seoul)) == 2


@pytest.mark.asyncio
async def test_concurrent_identical_reconciliation_creates_one_version() -> None:
    # Given: two requests with the same input identity and distinct proposed IDs.
    repository = InMemoryReportRepository()
    outcomes: list[AppendReportOutcome] = []

    async def reconcile(candidate: ReconcileRequest) -> None:
        outcomes.append(await reconcile_report(repository, candidate))

    # When: both requests race through the repository contract.
    async with anyio.create_task_group() as tasks:
        _ = tasks.start_soon(reconcile, request(10, 5))
        _ = tasks.start_soon(reconcile, request(20, 5))

    # Then: the atomic identity gate emits one version and one reuse result.
    assert sum(outcome.created for outcome in outcomes) == 1
    assert len(repository.history(date(2026, 7, 22))) == 1


def test_trailing_seven_targets_include_primary_and_immediate_q_dependency() -> None:
    # Given: reports through July 22 and late facts on several Seoul days.
    latest = date(2026, 7, 22)

    # When: correction targets are bounded to the latest seven report dates.
    recent = correction_targets(date(2026, 7, 21), latest)
    q_boundary = correction_targets(date(2026, 7, 15), latest)
    old = correction_targets(date(2026, 7, 14), latest)

    # Then: D and D+1 are selected only where they intersect the trailing window.
    assert recent.report_dates == (date(2026, 7, 21), date(2026, 7, 22))
    assert q_boundary.report_dates == (date(2026, 7, 16),)
    assert q_boundary.outside_window is False
    assert old.report_dates == ()
    assert old.outside_window is True
