from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from app.collection.base import CollectionError, CollectionErrorCode
from app.collection.completion_context_store import load_locked_completion_context
from app.collection.skip_decision_proofs import (
    SkipProofContext,
    attach_quota_proof,
    current_authorization,
)
from app.db.auth_models import CommunitySource, SourceAuthorizationDecision
from app.db.operations_models import BudgetDecision, ProviderBudgetRecord
from app.db.run_models import CollectionRun, SourceCheckpoint
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import (
    AuthorizationStatus,
    BudgetDecisionStatus,
    Country,
    RunStatus,
    SourcePlatform,
)
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.sql.base import Executable

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
RUN_ID = UUID("9a7a5116-4f24-4f80-98c7-45bb449d3c47")
COMMAND_ID = UUID("6cb3de1a-97d0-4564-8d7b-4c78180e4abd")
SOURCE_ID = UUID("1832bb2a-f81a-479e-a302-9f039b6740c8")
AUTHORIZATION_ID = UUID("65abffb2-1609-4800-9961-2369c84cb536")
CURRENT_AUTHORIZATION_ID = UUID("cbb4360b-0f45-4515-b895-484598d14278")
BUDGET_RECORD_ID = UUID("c8460db9-071a-4e04-b1c8-41061eb82e48")
BUDGET_DECISION_ID = UUID("bcbd13e7-60f1-40eb-8c4c-6440809b568c")


@dataclass(frozen=True, slots=True)
class _ScalarResult[T]:
    value: T | None

    def scalar_one(self) -> T:
        assert self.value is not None
        return self.value

    def scalar_one_or_none(self) -> T | None:
        return self.value


@dataclass(frozen=True, slots=True)
class _RowsResult[T]:
    values: tuple[T, ...]

    def scalars(self) -> _RowsResult[T]:
        return self

    def all(self) -> tuple[T, ...]:
        return self.values


def _source(scope_version: str = "scope-v1") -> CommunitySource:
    return CommunitySource(
        id=SOURCE_ID,
        country=Country.US,
        platform=SourcePlatform.REDDIT,
        external_key="prediction-markets",
        display_name="Prediction markets",
        scope_version=scope_version,
        enabled=True,
        active_authorization_id=AUTHORIZATION_ID,
        created_at=NOW,
    )


def _authorization(*, expired: bool = False) -> SourceAuthorizationDecision:
    return SourceAuthorizationDecision(
        id=AUTHORIZATION_ID,
        source_id=SOURCE_ID,
        status=AuthorizationStatus.APPROVED,
        evidence_sha256="a" * 64,
        evidence_location="urn:monitor:authorization:test",
        issuer="test",
        reviewer="test",
        permitted_scope={"permitted_routes": ["/r/test/new"]},
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW - timedelta(seconds=1) if expired else NOW + timedelta(days=1),
        revoked_at=None,
        decided_at=NOW - timedelta(days=1),
    )


def _run() -> CollectionRun:
    return CollectionRun(
        id=RUN_ID,
        command_id=COMMAND_ID,
        source_id=SOURCE_ID,
        scope_version="scope-v1",
        attempt=1,
        status=RunStatus.RUNNING,
        start_checkpoint_revision=0,
        start_cursor=None,
        genesis_chain_hash="0" * 64,
        committed_page_hash_chain="0" * 64,
        next_page_ordinal=0,
        committed_page_count=0,
        final_cursor=None,
        lease_identity_hash=b"l" * 32,
        authorization_decision_id=AUTHORIZATION_ID,
        budget_decision_id=BUDGET_DECISION_ID,
        budget_decision_status=BudgetDecisionStatus.SOFT_LIMIT,
        reviewed_page_cap=2,
        reviewed_post_cap=10,
        started_at=NOW,
        heartbeat_at=NOW,
        created_at=NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_factory", "authorization_factory"),
    [
        (lambda: _source("scope-v2"), _authorization),
        (_source, lambda: _authorization(expired=True)),
    ],
)
async def test_skip_policy_rejects_stale_or_wrong_scope_current_authorization(
    monkeypatch: pytest.MonkeyPatch,
    source_factory: Callable[[], CommunitySource],
    authorization_factory: Callable[[], SourceAuthorizationDecision],
) -> None:
    # Given: locked rows point at the claim decision but its current scope/window fails.
    responses = [
        _ScalarResult(source_factory()),
        _ScalarResult(authorization_factory()),
    ]

    async def execute(
        _statement: Executable,
    ) -> _ScalarResult[CommunitySource] | _ScalarResult[SourceAuthorizationDecision]:
        return responses.pop(0)

    # When: the 401/403 skip path checks authority at database time.
    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        with pytest.raises(CollectionError) as captured:
            _ = await current_authorization(session, _run(), NOW)

    # Then: expired or scope-stale authority is a current 403, not skip proof.
    assert captured.value.code is CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE
    assert captured.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_factory", "authorization_factory"),
    [
        (lambda: _source("scope-v2"), _authorization),
        (_source, lambda: _authorization(expired=True)),
    ],
)
async def test_finalizer_rejects_stale_or_wrong_scope_current_authorization(
    monkeypatch: pytest.MonkeyPatch,
    source_factory: Callable[[], CommunitySource],
    authorization_factory: Callable[[], SourceAuthorizationDecision],
) -> None:
    # Given: finalization locks the claimed decision after its scope/window changes.
    responses: list[
        _ScalarResult[CollectionCommand]
        | _ScalarResult[datetime]
        | _RowsResult[CollectionRun]
        | _ScalarResult[CommunitySource]
        | _ScalarResult[SourceAuthorizationDecision]
    ] = [
        _ScalarResult(CollectionCommand(id=COMMAND_ID)),
        _ScalarResult(NOW),
        _RowsResult((_run(),)),
        _ScalarResult(source_factory()),
        _ScalarResult(authorization_factory()),
    ]

    async def execute(
        _statement: Executable,
    ) -> (
        _ScalarResult[CollectionCommand]
        | _ScalarResult[datetime]
        | _RowsResult[CollectionRun]
        | _ScalarResult[CommunitySource]
        | _ScalarResult[SourceAuthorizationDecision]
    ):
        return responses.pop(0)

    # When: the finalizer validates current authorization at database time.
    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        with pytest.raises(CollectionError) as captured:
            _ = await load_locked_completion_context(session, COMMAND_ID, 1)

    # Then: it fails closed before the completion mutation can be planned.
    assert captured.value.code is CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE
    assert captured.value.status_code == 403
    assert responses == []


@pytest.mark.asyncio
async def test_recovery_accepts_claim_that_was_valid_before_pointer_rotation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a stuck run was legitimately claimed before an append-only rotation.
    source = _source()
    source.active_authorization_id = CURRENT_AUTHORIZATION_ID
    checkpoint = SourceCheckpoint(
        id=UUID("907d63c8-8d96-46fe-b6fa-49bff421c5d5"),
        source_id=SOURCE_ID,
        scope_version="scope-v1",
        cursor=None,
        revision=0,
        updated_at=NOW,
    )
    responses: list[object] = [
        _ScalarResult(CollectionCommand(id=COMMAND_ID)),
        _ScalarResult(NOW),
        _RowsResult((_run(),)),
        _ScalarResult(source),
        _ScalarResult(_authorization()),
        _ScalarResult(checkpoint),
        _RowsResult[object](()),
        _RowsResult[object](()),
    ]

    async def execute(_statement: Executable) -> object:
        return responses.pop(0)

    # When: stale-run recovery validates the immutable claim-time decision.
    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        locked = await load_locked_completion_context(
            session,
            COMMAND_ID,
            1,
            allow_retired_claim=True,
        )

    # Then: recovery may terminally reconcile without permitting another fetch.
    assert locked.domain.runs[0].run.authorization_decision_id == AUTHORIZATION_ID
    assert responses == []


@pytest.mark.asyncio
async def test_recovery_rejects_retired_claim_invalid_at_run_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    source.active_authorization_id = CURRENT_AUTHORIZATION_ID
    responses: list[object] = [
        _ScalarResult(CollectionCommand(id=COMMAND_ID)),
        _ScalarResult(NOW),
        _RowsResult((_run(),)),
        _ScalarResult(source),
        _ScalarResult(_authorization(expired=True)),
    ]

    async def execute(_statement: Executable) -> object:
        return responses.pop(0)

    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        with pytest.raises(CollectionError) as captured:
            _ = await load_locked_completion_context(
                session,
                COMMAND_ID,
                1,
                allow_retired_claim=True,
            )

    assert captured.value.code is CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE
    assert captured.value.status_code == 403
    assert responses == []


@pytest.mark.asyncio
async def test_provider_429_requires_persisted_eighty_percent_basis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the claim budget record is current but has only reached the 70% soft band.
    source = _source()
    run = _run()
    current = BudgetDecision(
        id=BUDGET_DECISION_ID,
        budget_record_id=BUDGET_RECORD_ID,
        source_id=SOURCE_ID,
        status=BudgetDecisionStatus.SOFT_LIMIT,
        observed_units=79,
        reason_code="free_tier_soft_scope_reduction",
        policy_version="free-tier-70-80-v1",
        reviewed_page_cap=2,
        reviewed_post_cap=10,
        evidence_sha256="b" * 64,
        evidence_location="urn:monitor:budget:test",
        decided_at=NOW,
    )
    record = ProviderBudgetRecord(
        id=BUDGET_RECORD_ID,
        provider=SourcePlatform.REDDIT.value,
        billing_period_start=NOW - timedelta(days=1),
        billing_period_end=NOW + timedelta(days=1),
        observed_units=79,
        soft_stop_units=70,
        hard_stop_units=80,
        paid_spend_enabled=False,
        evidence_sha256="c" * 64,
        evidence_location="urn:monitor:budget-record:test",
        verified_at=NOW,
    )

    responses = [_ScalarResult(current), _ScalarResult(record)]

    async def execute(
        _statement: Executable,
    ) -> _ScalarResult[BudgetDecision] | _ScalarResult[ProviderBudgetRecord]:
        return responses.pop(0)

    # When: a redacted provider 429 attempts to promote the run to quota skip.
    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        context = SkipProofContext(
            session,
            source,
            run,
            NOW,
            "d" * 64,
            "urn:monitor:collection-skip:test",
        )
        with pytest.raises(CollectionError) as captured:
            _ = await attach_quota_proof(context)

    # Then: the server refuses to invent an 80% hard-stop decision from client status.
    assert captured.value.code is CollectionErrorCode.INVALID_CONTRACT
    assert captured.value.status_code == 409
    assert responses == []
