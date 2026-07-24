"""Version-bound analysis queue and immutable keyword-match writes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.analysis_models import AnalysisQueueItem
from app.db.rule_models import KeywordRule, PostMatch
from app.domain.enums import QueueStatus

from .base import canonical_json_hash

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.domain.types import JsonValue

    from .normalizer import NormalizedPost


@dataclass(frozen=True, slots=True)
class AnalysisQueueVersions:
    """Immutable analysis tuple assigned to newly accepted revisions."""

    prompt: str
    model: str
    schema: str


@dataclass(frozen=True, slots=True)
class AnalysisInputWrite:
    """One accepted revision and its version-bound analysis facts."""

    post_id: UUID
    version_id: UUID
    item: NormalizedPost
    observed_at: datetime
    versions: AnalysisQueueVersions


async def persist_analysis_inputs(
    session: AsyncSession,
    write: AnalysisInputWrite,
) -> None:
    """Queue one revision and persist deterministic rule results."""
    versions = write.versions
    item = write.item
    queue = (
        insert(AnalysisQueueItem)
        .values(
            id=uuid4(),
            post_id=write.post_id,
            post_version_id=write.version_id,
            content_hash=item.content_hash,
            prompt_version=versions.prompt,
            model_version=versions.model,
            schema_version=versions.schema,
            status=QueueStatus.PENDING,
            attempts=0,
            available_at=write.observed_at,
            created_at=write.observed_at,
            updated_at=write.observed_at,
        )
        .on_conflict_do_nothing(constraint="uq_analysis_queue_post_version")
    )
    _ = await session.execute(queue)
    rules = (
        await session.execute(
            select(KeywordRule).where(
                KeywordRule.language == item.language,
                KeywordRule.enabled.is_(True),
            )
        )
    ).scalars()
    haystack = f"{item.title}\n{item.body}".casefold()
    for rule in rules:
        matched = rule.normalized_phrase.casefold() in haystack
        values: JsonValue = {
            "category": rule.category,
            "matched": matched,
            "normalized_phrase": rule.normalized_phrase,
            "post_version_id": str(write.version_id),
            "rule_id": str(rule.id),
        }
        statement = (
            insert(PostMatch)
            .values(
                id=uuid4(),
                post_version_id=write.version_id,
                rule_id=rule.id,
                matched=matched,
                normalized_phrase=rule.normalized_phrase,
                category=rule.category,
                match_hash=canonical_json_hash(values),
                matched_at=write.observed_at,
            )
            .on_conflict_do_nothing(constraint="uq_post_version_rule")
        )
        _ = await session.execute(statement)
