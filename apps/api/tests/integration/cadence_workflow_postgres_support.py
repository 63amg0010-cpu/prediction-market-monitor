"""Isolated PostgreSQL setup for cadence workflow recorder tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, cast, final
from uuid import UUID, uuid4

from app.api.routes.cadence_workflow import (
    CadenceWorkflowAttemptRequest,
    SourceResult,
    SqlCadenceWorkflowRecorder,
)
from app.collection.cadence_result import failure_receipt_hash
from app.db.session import DatabaseSessions
from app.services.identity.github import GitHubOIDCClaims
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.api.routes.cadence_workflow import CadenceWorkflowOidcAuthorizer

DATABASE_ENV = "MIGRATION_QA_DATABASE_URL"
SLOT_KEY = "2026-08-01T00:00:00Z"
UNSAFE_SLOT_KEY = "2026-08-01T00:15:00Z"


@dataclass(frozen=True, slots=True)
class RecorderFixture:
    """One fresh epoch and recorder bound to the isolated test database."""

    sessions: DatabaseSessions
    recorder: SqlCadenceWorkflowRecorder
    epoch_id: UUID
    sources: tuple[UUID, UUID]
    due_at: datetime


@dataclass(frozen=True, slots=True)
class RequestSpec:
    """Exact logical attempt and typed source outcome."""

    run_id: int
    attempt: int
    predecessor: UUID | None
    successes: tuple[bool, bool]
    slot_key: str = SLOT_KEY
    safe_failure: bool = True


@final
class AcceptedOidc:
    """Test-only exact workflow authorizer."""

    async def authorize(
        self,
        _token: SecretStr,
        _payload: CadenceWorkflowAttemptRequest,
    ) -> GitHubOIDCClaims:
        return GitHubOIDCClaims(
            iss="https://token.actions.githubusercontent.com",
            aud="monitor-control",
            sub="repo:owner@1/repository@2:environment:production-verifier",
            repository="owner/repository",
            repository_id="2",
            repository_owner_id="1",
            workflow_ref=(
                "owner/repository/.github/workflows/"
                "verify.yml@refs/heads/main"
            ),
            ref="refs/heads/main",
            sha="a" * 40,
            environment="production-verifier",
            run_id="1",
            run_attempt="1",
            jti=str(uuid4()),
            iat=1,
            nbf=1,
            exp=2,
        )


async def provision(url: str) -> RecorderFixture:
    """Insert one fresh epoch with independent safe and unsafe slots."""
    engine = create_async_engine(url)
    epoch_id = uuid4()
    async with engine.begin() as connection:
        rows = (
            await connection.execute(
                text(
                    """
                    SELECT id, platform::text AS platform
                    FROM community_sources
                    WHERE platform::text IN ('dcinside','manifold')
                    """
                )
            )
        ).mappings()
        by_platform = {
            str(typed["platform"]): cast("UUID", typed["id"])
            for row in rows
            if (typed := cast("Mapping[str, object]", row))
        }
        sources = by_platform["dcinside"], by_platform["manifold"]
        db_value = cast(
            "Mapping[str, object]",
            (
                await connection.execute(
                    text("SELECT transaction_timestamp() AS db_now")
                )
            ).mappings().one(),
        )["db_now"]
        assert isinstance(db_value, datetime)
        db_now = db_value
        due_at = db_now.astimezone(UTC) - timedelta(minutes=1)
        _ = await connection.execute(
            text(
                """
                INSERT INTO source_cadence_epochs (
                  id, activation_nonce, source_id, cadence_anchor_at,
                  expires_at, recheck_at
                ) VALUES (
                  :id, :nonce, :source_id, CAST(:anchor AS timestamptz),
                  CAST(:anchor AS timestamptz) + interval '31 days',
                  CAST(:anchor AS timestamptz) + interval '31 days'
                )
                """
            ),
            {
                "id": epoch_id,
                "nonce": uuid4(),
                "source_id": sources[1],
                "anchor": db_now.replace(minute=0, second=0, microsecond=0),
            },
        )
        _ = await connection.execute(
            text(
                """
                INSERT INTO cadence_epoch_contracts (
                  cadence_epoch_id, epoch_sha256, dcinside_source_id,
                  manifold_source_id, binding_sha256, scope_sha256,
                  window_closes_at
                ) VALUES (:id, :epoch, :dcinside, :manifold, :binding, :scope,
                          :closes)
                """
            ),
            {
                "id": epoch_id,
                "epoch": sha256(str(epoch_id).encode()).hexdigest(),
                "dcinside": sources[0],
                "manifold": sources[1],
                "binding": "b" * 64,
                "scope": "e" * 64,
                "closes": db_now + timedelta(days=30),
            },
        )
        for slot_key in (SLOT_KEY, UNSAFE_SLOT_KEY):
            _ = await connection.execute(
                text(
                    """
                    INSERT INTO cadence_workflow_slots (
                      cadence_epoch_id, schedule_kind, slot_key, due_at
                    ) VALUES (:id, 'verifier', :slot_key, :due_at)
                    """
                ),
                {"id": epoch_id, "slot_key": slot_key, "due_at": due_at},
            )
    await engine.dispose()
    sessions = DatabaseSessions.from_secret(SecretStr(url))
    oidc = cast(
        "CadenceWorkflowOidcAuthorizer",
        cast("object", AcceptedOidc()),
    )
    return RecorderFixture(
        sessions,
        SqlCadenceWorkflowRecorder(sessions, oidc),
        epoch_id,
        sources,
        due_at,
    )


def request(
    fixture: RecorderFixture,
    spec: RequestSpec,
) -> CadenceWorkflowAttemptRequest:
    """Build one exact success, safe-failure, or held-failure request."""
    return CadenceWorkflowAttemptRequest(
        repository="owner/repository",
        workflow="verify.yml",
        head_sha="a" * 40,
        ref="refs/heads/main",
        event="schedule" if spec.attempt == 1 else "workflow_dispatch",
        environment="production-verifier",
        run_id=spec.run_id,
        run_attempt=1,
        epoch_id=fixture.epoch_id,
        schedule_kind="verifier",
        slot_key=spec.slot_key,
        workflow_mode="schedule" if spec.attempt == 1 else "retry",
        cadence_attempt=spec.attempt,
        failed_predecessor_attempt_id=spec.predecessor,
        started_at=fixture.due_at + timedelta(seconds=10),
        completed_at=fixture.due_at + timedelta(seconds=20),
        source_results=tuple(
            SourceResult(
                source_id=source_id,
                status="succeeded" if succeeded else "failed",
                code=(
                    "ok"
                    if succeeded
                    else (
                        "transient_timeout"
                        if spec.safe_failure
                        else "unexpected_failure"
                    )
                ),
                retry_classification=(
                    "not_applicable"
                    if succeeded
                    else ("safe_terminal" if spec.safe_failure else "hold")
                ),
                receipt_sha256=(
                    character * 64
                    if succeeded
                    else failure_receipt_hash(
                        source_id,
                        (
                            "transient_timeout"
                            if spec.safe_failure
                            else "unexpected_failure"
                        ),
                    )
                ),
            )
            for source_id, succeeded, character in zip(
                fixture.sources, spec.successes, ("c", "d"), strict=True
            )
        ),
    )


__all__ = (
    "DATABASE_ENV",
    "SLOT_KEY",
    "UNSAFE_SLOT_KEY",
    "RecorderFixture",
    "RequestSpec",
    "provision",
    "request",
)
