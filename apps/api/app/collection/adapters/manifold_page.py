"""Page-wide Manifold normalization and collection caps."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from app.domain.enums import SourcePlatform

from .manifold_normalization import (
    ManifoldNormalizedComment,
    ManifoldRejectedOversize,
    ManifoldSkippedComment,
    normalize_manifold_comment,
)
from .models import (
    AdapterPage,
    NormalizedItem,
    NormalizedPost,
    PageTermination,
    RateLimitSnapshot,
    RejectedOversize,
)

if TYPE_CHECKING:
    from .manifold_contracts import ManifoldCommentWire, ManifoldMarket

MAX_MANIFOLD_PERSISTED_BYTES: Final = 262_144


class ManifoldPageAccumulator:
    """Mutable accumulator enforcing page-wide identity, count, and byte caps."""

    def __init__(self, accepted_limit: int) -> None:
        """Initialize one page-local cap state."""
        self.accepted_limit: int = accepted_limit
        self.items: list[NormalizedItem] = []
        self.accepted_count: int = 0
        self.rejected_count: int = 0
        self.persisted_bytes: int = 0
        self.seen_comment_ids: set[str] = set()

    def add(
        self,
        market: ManifoldMarket,
        comment: ManifoldCommentWire,
    ) -> PageTermination | None:
        """Add one safe projection or return the reached terminal cap."""
        if comment.id in self.seen_comment_ids:
            self.rejected_count += 1
            return None
        self.seen_comment_ids.add(comment.id)
        normalized = normalize_manifold_comment(market, comment)
        match normalized:  # noqa: RUF100  # noqa: MATCH_OK
            case ManifoldSkippedComment():
                self.rejected_count += 1
                return None
            case ManifoldRejectedOversize():
                self.items.append(
                    RejectedOversize(
                        source=SourcePlatform.MANIFOLD,
                        source_post_id=normalized.source_post_id,
                        canonical_url=normalized.canonical_url,
                        content_hash=normalized.content_hash,
                        size_bytes=normalized.size_bytes,
                    )
                )
                self.rejected_count += 1
                return None
            case ManifoldNormalizedComment():
                candidate_bytes = (
                    len(normalized.title.encode("utf-8")) + normalized.size_bytes
                )
                if (
                    self.persisted_bytes + candidate_bytes
                    > MAX_MANIFOLD_PERSISTED_BYTES
                ):
                    return PageTermination.REVIEWED_BYTE_CAP
                self.items.append(_accepted(normalized))
                self.accepted_count += 1
                self.persisted_bytes += candidate_bytes
                if self.accepted_count >= self.accepted_limit:
                    return PageTermination.REVIEWED_POST_CAP
                return None

    def page(self, termination: PageTermination) -> AdapterPage:
        """Freeze the current safe projection as one cursor-free page."""
        return _page(
            tuple(self.items),
            self.accepted_count,
            self.rejected_count,
            termination,
        )


def empty_manifold_page(termination: PageTermination) -> AdapterPage:
    """Return an empty cursor-free terminal Manifold page."""
    return _page((), 0, 0, termination)


def _accepted(normalized: ManifoldNormalizedComment) -> NormalizedPost:
    return NormalizedPost(
        source=SourcePlatform.MANIFOLD,
        source_post_id=normalized.source_post_id,
        canonical_url=normalized.canonical_url,
        title=normalized.title,
        body=normalized.body,
        published_at=normalized.published_at,
        language=normalized.language,
        comments_count=None,
        upvote_or_score=None,
        content_hash=normalized.content_hash,
        size_bytes=normalized.size_bytes,
    )


def _page(
    items: tuple[NormalizedItem, ...],
    accepted_count: int,
    rejected_count: int,
    termination: PageTermination,
) -> AdapterPage:
    return AdapterPage(
        items=items,
        next_cursor=None,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        rate_limit=RateLimitSnapshot(
            used=None,
            remaining=None,
            reset_after_seconds=None,
            retry_after_seconds=None,
        ),
        termination=termination,
    )


__all__ = ("ManifoldPageAccumulator", "empty_manifold_page")
