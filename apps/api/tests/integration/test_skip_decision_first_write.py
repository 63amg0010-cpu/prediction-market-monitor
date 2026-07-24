from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from app.api.routes.collector_models import SkipDecisionPayload
from app.collection import skip_decision_store as store
from app.collection.adapters.models import HttpMethod
from app.collection.adapters.models import (
    SourceAuthorizationDecision as AuthorizationSnapshot,
)
from app.collection.base import canonical_json_hash, hash_token
from app.collection.skip_decision_models import SkipDecisionOperation
from app.db.auth_models import CommunitySource, SourceAuthorizationDecision
from app.db.operations_models import CollectionSkipObservation
from app.db.run_models import CollectionRun
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import (
    AuthorizationStatus,
    BudgetDecisionStatus,
    CommandKind,
    CommandStatus,
    Country,
    RunStatus,
    SourcePlatform,
)
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.sql.base import Executable

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
RUN_ID = UUID("d50b066f-b872-411c-905b-995eeddf1df0")
COMMAND_ID = UUID("a7ae35a5-2d3f-44e8-b86e-c68e6def9977")
SOURCE_ID = UUID("31d77966-dc3c-4652-8a49-0c70d8202b24")
AUTHORIZATION_ID = UUID("cc9a482c-28a6-4cc4-8a86-6ab99044d376")
BUDGET_DECISION_ID = UUID("3eb6edc1-f53e-4c68-8bb2-b1ca72f779f3")
IDEMPOTENCY_KEY = UUID("f9a7d170-179c-4ef8-80ea-fdc25c48b9a0")
LEASE = "l" * 43

type _ScalarValue = (
    CollectionRun
    | CollectionCommand
    | datetime
    | CommunitySource
    | SourceAuthorizationDecision
)


@dataclass(frozen=True, slots=True)
class _ScalarResult:
    value: _ScalarValue | None

    def scalar_one(self) -> _ScalarValue:
        assert self.value is not None
        return self.value

    def scalar_one_or_none(self) -> _ScalarValue | None:
        return self.value


def _authorization_snapshot() -> AuthorizationSnapshot:
    return AuthorizationSnapshot(
        decision_id=AUTHORIZATION_ID,
        source=SourcePlatform.REDDIT,
        status=AuthorizationStatus.APPROVED,
        evidence_sha256="a" * 64,
        evidence_location="urn:monitor:authorization:test",
        issuer="provider",
        reviewer="owner",
        permitted_methods=frozenset({HttpMethod.GET}),
        permitted_routes=frozenset({"/r/test/new"}),
        permitted_fields=frozenset({"title"}),
        permitted_subreddits=frozenset({"test"}),
        purpose="tests",
        requests_per_minute=30,
        concurrency=1,
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        revoked_at=None,
    )


@pytest.mark.asyncio
async def test_first_skip_write_derives_one_policy_proof_and_full_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: an exact active lease, current authorization, and no page or skip proof.
    snapshot = _authorization_snapshot()
    run = CollectionRun(
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
        authorization_snapshot=snapshot.model_dump(mode="json"),
        budget_decision_id=BUDGET_DECISION_ID,
        budget_decision_status=BudgetDecisionStatus.ALLOW,
        reviewed_page_cap=4,
        reviewed_post_cap=20,
        started_at=NOW,
        heartbeat_at=NOW,
        created_at=NOW,
    )
    command = CollectionCommand(
        id=COMMAND_ID,
        slot_id=None,
        scope_version="scope-v1",
        source_set_hash="b" * 64,
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
    source = CommunitySource(
        id=SOURCE_ID,
        country=Country.US,
        platform=SourcePlatform.REDDIT,
        external_key="test",
        display_name="Test",
        scope_version="scope-v1",
        enabled=True,
        active_authorization_id=AUTHORIZATION_ID,
        created_at=NOW,
    )
    authorization = SourceAuthorizationDecision(
        id=AUTHORIZATION_ID,
        source_id=SOURCE_ID,
        status=AuthorizationStatus.APPROVED,
        evidence_sha256="a" * 64,
        evidence_location="urn:monitor:authorization:test",
        issuer="provider",
        reviewer="owner",
        permitted_scope={"permitted_routes": ["/r/test/new"]},
        effective_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        revoked_at=None,
        decided_at=NOW - timedelta(days=1),
    )
    responses: list[_ScalarValue | None] = [
        run,
        command,
        NOW,
        None,
        None,
        source,
        authorization,
    ]
    added: list[SourceAuthorizationDecision | CollectionSkipObservation] = []

    async def execute(_statement: Executable) -> _ScalarResult:
        return _ScalarResult(responses.pop(0))

    operation = SkipDecisionOperation(
        RUN_ID,
        SkipDecisionPayload(
            command_id=COMMAND_ID,
            attempt=1,
            lease_token=LEASE,
            idempotency_key=IDEMPOTENCY_KEY,
            provider=SourcePlatform.REDDIT,
            route="/r/test/new",
            http_status=401,
            failure_code="provider_authorization_rejected",
        ),
        "github:collector",
    )

    # When: the server handles the first redacted policy observation.
    async with AsyncSession() as session:
        monkeypatch.setattr(session, "execute", execute)
        monkeypatch.setattr(session, "add", added.append)
        outcome = await store.attach_skip_decision(session, operation)

    # Then: exactly one proof is attached and every audit field is server persisted.
    proof = next(
        item for item in added if isinstance(item, SourceAuthorizationDecision)
    )
    observation = next(
        item for item in added if isinstance(item, CollectionSkipObservation)
    )
    assert outcome.status_code == 201
    assert outcome.response.terminal_status is RunStatus.SKIPPED_POLICY
    assert run.skip_authorization_decision_id == proof.id
    assert run.skip_budget_decision_id is None
    assert source.enabled is False
    assert source.active_authorization_id == proof.id
    assert observation.run_id == RUN_ID
    assert observation.command_id == COMMAND_ID
    assert observation.attempt == 1
    assert observation.idempotency_key == IDEMPOTENCY_KEY
    assert observation.actor_principal_id == "github:collector"
    assert observation.provider is SourcePlatform.REDDIT
    assert observation.route == "/r/test/new"
    assert observation.http_status == 401
    assert observation.failure_code == "provider_authorization_rejected"
    assert observation.decision_kind == "policy"
    assert observation.decision_id == proof.id
    assert observation.evidence_sha256 == outcome.response.evidence_sha256
    assert observation.evidence_location.startswith("urn:monitor:collection-skip:")
    assert observation.stored_response == outcome.response_bytes
    assert observation.created_at == NOW
    assert observation.request_hash == canonical_json_hash(
        operation.payload.model_dump(mode="json", exclude={"lease_token"})
    )
    assert responses == []
