"""PostgreSQL row locking and page-commit context snapshots."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, func, select

from app.db.auth_models import CommunitySource, SourceAuthorizationDecision
from app.db.page_models import PageCommit
from app.db.run_models import CollectionRun, SourceCheckpoint
from app.db.scheduler_models import CollectionCommand

from .authorization import AuthorizationSnapshot
from .base import CollectionError, CollectionErrorCode
from .orm_state import (
    to_checkpoint_state,
    to_command_state,
    to_page_commit_record,
    to_run_state,
)
from .page_commit import PageCommitContext
from .page_service_models import (
    LockedPageContext,
    PageCommitOperation,
    PageCommitServiceConfig,
)
from .post_store import load_existing_posts

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def load_locked_page_context(
    session: AsyncSession,
    operation: PageCommitOperation,
    config: PageCommitServiceConfig,
) -> LockedPageContext:
    """Lock the run/checkpoint stream and snapshot every planning fact."""
    run_row = (
        await session.execute(
            select(CollectionRun)
            .where(CollectionRun.id == operation.run_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if run_row is None:
        raise CollectionError(CollectionErrorCode.LEASE_OR_ATTEMPT_MISMATCH, 409)
    command_row = (
        await session.execute(
            select(CollectionCommand)
            .where(CollectionCommand.id == run_row.command_id)
            .with_for_update()
        )
    ).scalar_one()
    checkpoint_row = (
        await session.execute(
            select(SourceCheckpoint)
            .where(
                SourceCheckpoint.source_id == run_row.source_id,
                SourceCheckpoint.scope_version == run_row.scope_version,
            )
            .with_for_update()
        )
    ).scalar_one()
    committed_rows = tuple(
        (
            await session.execute(
                select(PageCommit)
                .where(PageCommit.run_id == run_row.id)
                .order_by(PageCommit.page_ordinal)
            )
        )
        .scalars()
        .all()
    )
    existing_idempotency = next(
        (
            row
            for row in committed_rows
            if row.page_idempotency_key == operation.request.page_idempotency_key
        ),
        None,
    )
    existing_ordinal = next(
        (
            row
            for row in committed_rows
            if row.page_ordinal == operation.request.page_ordinal
        ),
        None,
    )
    run = replace(
        to_run_state(run_row),
        accepted_count=sum(row.accepted_count for row in committed_rows),
        duplicate_count=sum(row.duplicate_count for row in committed_rows),
        rejected_count=sum(row.rejected_count for row in committed_rows),
    )
    reviewed_page_cap = run.reviewed_page_cap
    reviewed_post_cap = run.reviewed_post_cap
    if reviewed_page_cap is None or reviewed_post_cap is None:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    if not (
        0 < reviewed_page_cap <= config.reviewed_page_cap
        and 0 < reviewed_post_cap <= config.reviewed_post_cap
    ):
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 409)
    source_ids = tuple(
        row.source_id
        for row in (
            await session.execute(
                select(CollectionRun)
                .where(
                    CollectionRun.command_id == command_row.id,
                    CollectionRun.attempt == command_row.attempt,
                )
                .order_by(CollectionRun.source_id)
            )
        )
        .scalars()
        .all()
    )
    source_post_ids = tuple(post.source_post_id for post in operation.request.posts)
    existing_posts = await load_existing_posts(
        session,
        run_row.source_id,
        source_post_ids,
    )
    clock = func.clock_timestamp(type_=DateTime(timezone=True))
    db_now: datetime = (await session.execute(select(clock))).scalar_one()
    domain = PageCommitContext(
        db_now=db_now,
        command=to_command_state(command_row, source_ids),
        run=run,
        checkpoint=to_checkpoint_state(checkpoint_row),
        authorization=await _authorization_snapshot(session, run_row.source_id),
        existing_posts=existing_posts,
        existing_idempotency_commit=(
            to_page_commit_record(existing_idempotency)
            if existing_idempotency is not None
            else None
        ),
        existing_ordinal_commit=(
            to_page_commit_record(existing_ordinal)
            if existing_ordinal is not None
            else None
        ),
        reviewed_page_cap=reviewed_page_cap,
        reviewed_post_cap=reviewed_post_cap,
    )
    return LockedPageContext(domain, run_row, checkpoint_row)


async def _authorization_snapshot(
    session: AsyncSession,
    source_id: UUID,
) -> AuthorizationSnapshot:
    source = (
        await session.execute(
            select(CommunitySource)
            .where(CommunitySource.id == source_id)
            .with_for_update()
        )
    ).scalar_one()
    if source.active_authorization_id is not None:
        decision_statement = select(SourceAuthorizationDecision).where(
            SourceAuthorizationDecision.id == source.active_authorization_id,
            SourceAuthorizationDecision.source_id == source_id,
        )
    else:
        decision_statement = (
            select(SourceAuthorizationDecision)
            .where(SourceAuthorizationDecision.source_id == source_id)
            .order_by(SourceAuthorizationDecision.decided_at.desc())
            .limit(1)
        )
    decision = (await session.execute(decision_statement)).scalar_one_or_none()
    if decision is None:
        raise CollectionError(CollectionErrorCode.SOURCE_AUTHORIZATION_INACTIVE, 403)
    return AuthorizationSnapshot(
        decision.id,
        source.id,
        source.scope_version,
        source.enabled and source.active_authorization_id == decision.id,
        decision.status,
        decision.effective_at,
        decision.expires_at,
        decision.revoked_at,
    )
