"""Locked source authorization reads used by collector claims."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, select

from app.db.auth_models import CommunitySource, SourceAuthorizationDecision

from .authorization import AuthorizationSnapshot

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


def claim_authorization_statement(
    source_ids: tuple[UUID, ...], scope_version: str
) -> Select[tuple[CommunitySource, SourceAuthorizationDecision]]:
    """Build the PostgreSQL authorization lock used before a source claim."""
    return (
        select(CommunitySource, SourceAuthorizationDecision)
        .join(
            SourceAuthorizationDecision,
            CommunitySource.active_authorization_id == SourceAuthorizationDecision.id,
        )
        .where(
            CommunitySource.id.in_(source_ids),
            CommunitySource.scope_version == scope_version,
        )
        .order_by(CommunitySource.id)
        .with_for_update(of=(CommunitySource, SourceAuthorizationDecision))
    )


async def authorization_snapshots(
    session: AsyncSession,
    source_ids: tuple[UUID, ...],
    scope_version: str,
) -> dict[UUID, AuthorizationSnapshot]:
    """Read exact active-decision rows while retaining their lock."""
    rows = (
        (
            await session.execute(
                claim_authorization_statement(source_ids, scope_version)
            )
        )
        .tuples()
        .all()
    )
    return {
        source.id: AuthorizationSnapshot(
            decision.id,
            source.id,
            source.scope_version,
            source.enabled and source.active_authorization_id == decision.id,
            decision.status,
            decision.effective_at,
            decision.expires_at,
            decision.revoked_at,
        )
        for source, decision in rows
    }


__all__ = ("authorization_snapshots", "claim_authorization_statement")
