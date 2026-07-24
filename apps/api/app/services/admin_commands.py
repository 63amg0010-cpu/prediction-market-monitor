"""Durable idempotent administrator command persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, final
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.api.routes.commands import (
    CollectionRetryCommand,
    CommandAccepted,
    DailyReconcileCommand,
)
from app.collection.commands import collection_source_set_hash
from app.core.errors import IdentityError, IdentityErrorCode
from app.db.auth_models import CommunitySource, SourceAuthorizationDecision
from app.db.operations_models import ScheduledJobRun
from app.db.scheduler_models import CollectionCommand
from app.domain.enums import (
    AuthorizationStatus,
    CommandKind,
    CommandStatus,
    JobKind,
    JobStatus,
)
from app.services.identity.exchanges import SystemClock

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.session import DatabaseSessions


@final
class SqlAdminCommandHandler:
    """Create collection retry and daily reconciliation jobs idempotently."""

    def __init__(
        self,
        sessions: DatabaseSessions,
        scope_version: str,
        clock: SystemClock | None = None,
    ) -> None:
        """Bind administrator commands to one reviewed source scope."""
        self._sessions = sessions
        self._scope_version = scope_version
        self._clock = clock or SystemClock()

    async def retry_collection(
        self,
        command: CollectionRetryCommand,
    ) -> CommandAccepted:
        """Persist an authorized source-set retry or recover its exact request."""
        source_ids = tuple(sorted(command.source_ids, key=lambda item: item.hex))
        if len(source_ids) != len(set(source_ids)):
            raise _invalid_command()
        now = self._clock.now()
        async with self._sessions.open() as session, session.begin():
            await self._require_active_sources(session, source_ids, now)
            source_hash = collection_source_set_hash(source_ids)
            command_id = uuid4()
            idempotency_key = f"admin-collection-retry/v1/{command.request_id}"
            inserted = (
                await session.execute(
                    insert(CollectionCommand)
                    .values(
                        id=command_id,
                        slot_id=None,
                        scope_version=self._scope_version,
                        source_set_hash=source_hash,
                        kind=CommandKind.MANUAL,
                        idempotency_key=idempotency_key,
                        status=CommandStatus.QUEUED,
                        attempt=1,
                        available_at=now,
                        outcome_code=command.reason,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                    .returning(CollectionCommand.id)
                )
            ).scalar_one_or_none()
            row = await session.scalar(
                select(CollectionCommand)
                .where(CollectionCommand.idempotency_key == idempotency_key)
                .with_for_update()
            )
            if row is None or (
                row.kind is not CommandKind.MANUAL
                or row.scope_version != self._scope_version
                or row.source_set_hash != source_hash
                or row.outcome_code != command.reason
            ):
                raise _invalid_command()
            return CommandAccepted(command_id=row.id, created=inserted is not None)

    async def reconcile_daily(
        self,
        command: DailyReconcileCommand,
    ) -> CommandAccepted:
        """Persist one bounded reconciliation trigger or recover its identity."""
        now = self._clock.now()
        job_id = uuid4()
        idempotency_key = f"admin-daily-reconcile/v1/{command.request_id}"
        async with self._sessions.open() as session, session.begin():
            inserted = (
                await session.execute(
                    insert(ScheduledJobRun)
                    .values(
                        id=job_id,
                        kind=JobKind.RECONCILIATION,
                        target_date_seoul=None,
                        idempotency_key=idempotency_key,
                        status=JobStatus.QUEUED,
                        attempt=1,
                        available_at=now,
                        report_outcome={"request_id": str(command.request_id)},
                        created_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=["idempotency_key"])
                    .returning(ScheduledJobRun.id)
                )
            ).scalar_one_or_none()
            row = await session.scalar(
                select(ScheduledJobRun)
                .where(ScheduledJobRun.idempotency_key == idempotency_key)
                .with_for_update()
            )
            if row is None or row.kind is not JobKind.RECONCILIATION:
                raise _invalid_command()
            return CommandAccepted(command_id=row.id, created=inserted is not None)

    async def _require_active_sources(
        self,
        session: AsyncSession,
        source_ids: tuple[UUID, ...],
        now: datetime,
    ) -> None:
        active = set(
            (
                await session.execute(
                    select(CommunitySource.id)
                    .join(
                        SourceAuthorizationDecision,
                        SourceAuthorizationDecision.id
                        == CommunitySource.active_authorization_id,
                    )
                    .where(
                        CommunitySource.id.in_(source_ids),
                        CommunitySource.scope_version == self._scope_version,
                        CommunitySource.enabled.is_(True),
                        SourceAuthorizationDecision.status
                        == AuthorizationStatus.APPROVED,
                        SourceAuthorizationDecision.effective_at <= now,
                        SourceAuthorizationDecision.expires_at > now,
                        SourceAuthorizationDecision.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        if active != set(source_ids):
            raise _invalid_command()


def _invalid_command() -> IdentityError:
    return IdentityError(
        IdentityErrorCode.INVALID_REQUEST,
        "administrator command rejected",
    )
