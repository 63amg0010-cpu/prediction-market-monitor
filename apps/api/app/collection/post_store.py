"""Transactional post, revision, engagement, match, and queue writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.page_models import PageCommitItem
from app.db.post_models import EngagementObservation, Post, PostVersion
from app.domain.enums import PageItemDisposition, PostVersionReason

from .analysis_input_store import (
    AnalysisInputWrite,
    AnalysisQueueVersions,
    persist_analysis_inputs,
)
from .base import CollectionError, CollectionErrorCode, canonical_json_hash
from .normalizer import NormalizedPost, OversizeRejection
from .page_commit import ExistingPostVersion, PageCommitPlan, PageItemPlan

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.types import JsonValue


@dataclass(frozen=True, slots=True)
class PageWriteContext:
    """Run identity, observation clock, and analysis versions for one page."""

    run_id: UUID
    source_id: UUID
    observed_at: datetime
    versions: AnalysisQueueVersions


@dataclass(frozen=True, slots=True)
class NormalizedItemWrite:
    """One normalized page item paired with its server persistence plan."""

    page_commit_id: UUID
    plan: PageItemPlan
    item: NormalizedPost


async def load_existing_posts(
    session: AsyncSession,
    source_id: UUID,
    source_post_ids: tuple[str, ...],
) -> tuple[ExistingPostVersion, ...]:
    """Lock current revisions for all identities present on one page."""
    if not source_post_ids:
        return ()
    statement = (
        select(Post)
        .where(
            Post.source_id == source_id,
            Post.source_post_id.in_(source_post_ids),
        )
        .with_for_update()
    )
    posts = tuple((await session.execute(statement)).scalars().all())
    existing: list[ExistingPostVersion] = []
    for post in posts:
        if post.current_version_id is None:
            raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
        version = await session.get(PostVersion, post.current_version_id)
        if version is None:
            raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
        existing.append(
            ExistingPostVersion(
                post.source_post_id,
                post.id,
                version.id,
                version.content_hash,
                version.revision,
            )
        )
    return tuple(existing)


async def persist_page_items(
    session: AsyncSession,
    context: PageWriteContext,
    page_plan: PageCommitPlan,
) -> None:
    """Persist ordered page results and every accepted-version side effect."""
    for plan in page_plan.items:
        item = plan.normalized_item
        match item:
            case OversizeRejection():
                session.add(
                    PageCommitItem(
                        id=uuid4(),
                        page_commit_id=page_plan.commit.id,
                        item_ordinal=plan.item_ordinal,
                        disposition=plan.disposition,
                        source_post_id=item.source_post_id,
                        canonical_url=item.canonical_url,
                        normalized_content_hash=item.content_hash,
                        post_version_id=None,
                        rejected_body_bytes=item.body_bytes,
                        rejection_reason=item.rejection_reason,
                    )
                )
                continue
            case NormalizedPost():
                await _persist_normalized_item(
                    session,
                    context,
                    NormalizedItemWrite(page_plan.commit.id, plan, item),
                )
                continue
            case _:
                assert_never(item)


async def _persist_normalized_item(
    session: AsyncSession,
    context: PageWriteContext,
    write: NormalizedItemWrite,
) -> None:
    plan = write.plan
    item = write.item
    post_id = plan.post_id
    version_id = plan.post_version_id
    if post_id is None or version_id is None or plan.revision is None:
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
    match plan.disposition:
        case PageItemDisposition.ACCEPTED:
            post = await session.get(Post, post_id, with_for_update=True)
            if post is None:
                post = Post(
                    id=post_id,
                    source_id=context.source_id,
                    source_post_id=item.source_post_id,
                    canonical_url=item.canonical_url,
                    published_at=item.published_at,
                    language=item.language,
                    current_version_id=None,
                    created_at=context.observed_at,
                    updated_at=context.observed_at,
                )
                session.add(post)
            reason = (
                PostVersionReason.FIRST_SEEN
                if plan.revision == 1
                else PostVersionReason.SOURCE_EDIT
            )
            session.add(
                PostVersion(
                    id=version_id,
                    post_id=post_id,
                    revision=plan.revision,
                    content_hash=item.content_hash,
                    title=item.title,
                    body=item.body,
                    body_bytes=item.body_bytes,
                    reason=reason,
                    collected_at=context.observed_at,
                )
            )
            post.current_version_id = version_id
            post.canonical_url = item.canonical_url
            post.updated_at = context.observed_at
            await persist_analysis_inputs(
                session,
                AnalysisInputWrite(
                    post_id,
                    version_id,
                    item,
                    context.observed_at,
                    context.versions,
                ),
            )
        case PageItemDisposition.DUPLICATE:
            pass
        case PageItemDisposition.REJECTED_OVERSIZE:
            raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
        case _:
            assert_never(plan.disposition)
    session.add(
        PageCommitItem(
            id=uuid4(),
            page_commit_id=write.page_commit_id,
            item_ordinal=plan.item_ordinal,
            disposition=plan.disposition,
            source_post_id=item.source_post_id,
            canonical_url=item.canonical_url,
            normalized_content_hash=item.content_hash,
            post_version_id=version_id,
            rejected_body_bytes=None,
            rejection_reason=None,
        )
    )
    engagement: JsonValue = {
        "comments_count": item.comments_count,
        "observed_at": context.observed_at.isoformat(),
        "post_version_id": str(version_id),
        "source_run_id": str(context.run_id),
        "upvote_or_score": item.upvote_or_score,
    }
    statement = (
        insert(EngagementObservation)
        .values(
            id=uuid4(),
            post_version_id=version_id,
            source_run_id=context.run_id,
            observed_at=context.observed_at,
            comments_count=item.comments_count,
            upvote_or_score=item.upvote_or_score,
            engagement_hash=canonical_json_hash(engagement),
        )
        .on_conflict_do_nothing(constraint="uq_engagement_version_run")
    )
    _ = await session.execute(statement)
