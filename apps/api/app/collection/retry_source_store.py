"""Attempt-history selection for collection command retries."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_never

from sqlalchemy import select

from app.db.run_models import CollectionRun
from app.domain.enums import RunStatus

from .base import CollectionError, CollectionErrorCode

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def eligible_retry_sources(
    session: AsyncSession,
    command_id: UUID,
    source_ids: tuple[UUID, ...],
) -> tuple[UUID, ...]:
    """Select only sources whose latest attempt remains retryable."""
    history = tuple(
        (
            await session.execute(
                select(CollectionRun)
                .where(CollectionRun.command_id == command_id)
                .order_by(CollectionRun.source_id, CollectionRun.attempt.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[UUID, RunStatus] = {}
    for row in history:
        _ = latest.setdefault(row.source_id, row.status)
    eligible: list[UUID] = []
    for source_id in source_ids:
        status = latest.get(source_id)
        match status:
            case None | RunStatus.FAILED_RETRYABLE | RunStatus.STALE_ABANDONED:
                eligible.append(source_id)
            case (
                RunStatus.SUCCEEDED
                | RunStatus.FAILED_TERMINAL
                | RunStatus.SKIPPED_POLICY
                | RunStatus.SKIPPED_QUOTA
            ):
                pass
            case RunStatus.CREATED | RunStatus.RUNNING:
                raise CollectionError(CollectionErrorCode.INVALID_TRANSITION, 409)
            case _:
                assert_never(status)
    return tuple(eligible)


__all__ = ("eligible_retry_sources",)
