from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from app.api.routes.collector_models import SkipDecisionPayload, SkipDecisionResponse
from app.collection import skip_decision_store as store
from app.collection.base import (
    CollectionError,
    CollectionErrorCode,
    canonical_json_hash,
    hash_token,
)
from app.collection.skip_decision_models import SkipDecisionOperation
from app.db.operations_models import CollectionSkipObservation
from app.db.run_models import CollectionRun
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import CommandKind, CommandStatus, RunStatus, SourcePlatform
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.sql.base import Executable

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
RUN_ID = UUID("44aa108f-f855-4a0c-88d2-20600a882e37")
COMMAND_ID = UUID("ed808507-fdc2-4505-9b42-1d3732930284")
SOURCE_ID = UUID("14e739bd-514f-4a9f-97f9-0d7db28c7297")
AUTHORIZATION_ID = UUID("f99b7127-fc39-437a-aa3c-4968a2cc96db")
OBSERVATION_ID = UUID("e554b06b-4ea4-491c-a4af-032658f816f2")
IDEMPOTENCY_KEY = UUID("811538ce-923d-47e8-9b28-f9c4886970aa")
LEASE = "l" * 43


@dataclass(frozen=True, slots=True)
class _ScalarResult[T]:
    value: T | None

    def scalar_one(self) -> T:
        assert self.value is not None
        return self.value

    def scalar_one_or_none(self) -> T | None:
        return self.value


type _ExecuteResult = (
    _ScalarResult[CollectionRun]
    | _ScalarResult[CollectionCommand]
    | _ScalarResult[datetime]
    | _ScalarResult[CollectionSkipObservation]
)


def _payload(route: str = "/r/test/new") -> SkipDecisionPayload:
    return SkipDecisionPayload(
        command_id=COMMAND_ID,
        attempt=1,
        lease_token=LEASE,
        idempotency_key=IDEMPOTENCY_KEY,
        provider=SourcePlatform.REDDIT,
        route=route,
        http_status=401,
        failure_code="provider_authorization_rejected",
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
        lease_identity_hash=hash_token(LEASE),
        authorization_decision_id=AUTHORIZATION_ID,
        skip_authorization_decision_id=AUTHORIZATION_ID,
        started_at=NOW,
        heartbeat_at=NOW,
        created_at=NOW,
    )


def _command() -> CollectionCommand:
    return CollectionCommand(
        id=COMMAND_ID,
        slot_id=None,
        scope_version="scope-v1",
        source_set_hash="a" * 64,
        kind=CommandKind.SCHEDULED,
        idempotency_key="scheduled:scope-v1:test",
        status=CommandStatus.RUNNING,
        attempt=1,
        available_at=NOW,
        claimed_at=NOW,
        heartbeat_at=NOW,
        lease_hash=hash_token(LEASE),
        created_at=NOW,
    )


def _observation() -> CollectionSkipObservation:
    payload = _payload()
    response = SkipDecisionResponse(
        skip_decision_id=AUTHORIZATION_ID,
        terminal_status=RunStatus.SKIPPED_POLICY,
        evidence_sha256="b" * 64,
    )
    return CollectionSkipObservation(
        id=OBSERVATION_ID,
        run_id=RUN_ID,
        command_id=COMMAND_ID,
        attempt=1,
        idempotency_key=IDEMPOTENCY_KEY,
        request_hash=canonical_json_hash(
            payload.model_dump(mode="json", exclude={"lease_token"})
        ),
        actor_principal_id="github:collector",
        provider=SourcePlatform.REDDIT,
        route=payload.route,
        http_status=401,
        failure_code="provider_authorization_rejected",
        decision_kind="policy",
        decision_id=AUTHORIZATION_ID,
        evidence_sha256="b" * 64,
        evidence_location="urn:monitor:collection-skip:test",
        stored_response=response.model_dump_json().encode(),
        created_at=NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_route", [False, True])
async def test_skip_receipt_replays_or_conflicts_after_proof_attachment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    changed_route: bool,
) -> None:
    # Given: the first transaction attached its proof and persisted one receipt.
    observation = _observation()
    responses: list[_ExecuteResult] = [
        _ScalarResult(_run()),
        _ScalarResult(_command()),
        _ScalarResult(NOW),
        _ScalarResult(observation),
    ]

    async def execute(_statement: Executable) -> _ExecuteResult:
        return responses.pop(0)

    operation = SkipDecisionOperation(
        RUN_ID,
        _payload("/r/test/hot" if changed_route else "/r/test/new"),
        "github:collector",
    )

    # When: response-loss recovery retries the same key.
    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        if changed_route:
            with pytest.raises(CollectionError) as captured:
                _ = await store.attach_skip_decision(session, operation)
            assert captured.value.code is CollectionErrorCode.IDEMPOTENCY_KEY_REUSED
            assert captured.value.status_code == 409
        else:
            outcome = await store.attach_skip_decision(session, operation)
            assert outcome.status_code == 200
            assert outcome.response_bytes == observation.stored_response

    # Then: an exact retry is byte-identical 200 and changed payload is 409.
    assert responses == []


def test_skip_statements_lock_exact_run_command_attempt_and_commit_rows() -> None:
    # Given: a path-bound observation for one run and command attempt.
    operation = SkipDecisionOperation(RUN_ID, _payload(), "github:collector")

    # When: every mutation-side statement is compiled for PostgreSQL.
    run_sql = str(
        store.skip_run_lock_statement(operation).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    command_sql = str(
        store.skip_command_lock_statement(operation).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    commits_sql = str(
        store.skip_page_commits_lock_statement(RUN_ID).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    # Then: no broader row set can authorize or hide a zero-commit decision.
    assert str(RUN_ID) in run_sql
    assert str(COMMAND_ID) in run_sql
    assert "collection_runs.attempt = 1" in run_sql
    assert "FOR UPDATE OF collection_runs" in run_sql
    assert str(COMMAND_ID) in command_sql
    assert "collection_commands.attempt = 1" in command_sql
    assert "FOR UPDATE OF collection_commands" in command_sql
    assert str(RUN_ID) in commits_sql
    assert "FOR UPDATE OF page_commits" in commits_sql
