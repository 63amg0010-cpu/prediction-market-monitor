"""Author-free page item parsing and deterministic normalization."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - Pydantic resolves fields at runtime.
from hashlib import sha256
from typing import Annotated, ClassVar, Literal, assert_never

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StringConstraints

from app.services.configuration.canonical import canonical_bytes

from .base import MAX_POST_BYTES, CollectionError, CollectionErrorCode, require_utc

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SourcePostId = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]


class AcceptedPostInput(BaseModel):
    """Trusted-boundary shape for a full normalized source item."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    source_post_id: SourcePostId
    canonical_url: AnyHttpUrl
    title: str
    body: str
    published_at: datetime
    language: Literal["ko", "en"]
    comments_count: int | None = Field(default=None, ge=0)
    upvote_or_score: int | None = None
    content_hash: Sha256Hex


class OversizePostInput(BaseModel):
    """Descriptor-only input for content rejected before transport."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    source_post_id: SourcePostId
    canonical_url: AnyHttpUrl
    content_hash: Sha256Hex
    body_bytes: int = Field(gt=MAX_POST_BYTES)
    rejection_reason: Literal["rejected_oversize"]


type PagePostInput = AcceptedPostInput | OversizePostInput


@dataclass(frozen=True, slots=True)
class NormalizedPost:
    """Canonical full item eligible for post/version persistence."""

    source_post_id: str
    canonical_url: str
    title: str
    body: str
    body_bytes: int
    published_at: datetime
    language: Literal["ko", "en"]
    comments_count: int | None
    upvote_or_score: int | None
    content_hash: str


@dataclass(frozen=True, slots=True)
class OversizeRejection:
    """Only the permitted descriptor retained for an oversize item."""

    source_post_id: str
    canonical_url: str
    content_hash: str
    body_bytes: int
    rejection_reason: Literal["rejected_oversize"] = "rejected_oversize"


type NormalizedPageItem = NormalizedPost | OversizeRejection


class _ContentIdentity(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    title: str
    body: str


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def compute_content_hash(title: str, body: str) -> str:
    """Hash the canonical full title/body values persisted as a revision."""
    identity = _ContentIdentity(
        title=_normalize_text(title),
        body=_normalize_text(body),
    )
    return sha256(canonical_bytes(identity)).hexdigest()


def compute_body_bytes(body: str) -> int:
    """Return the persisted canonical UTF-8 body size."""
    return len(_normalize_text(body).encode("utf-8"))


def normalize_page_items(
    items: tuple[PagePostInput, ...],
) -> tuple[NormalizedPageItem, ...]:
    """Normalize accepted items and wholly reduce oversize items to descriptors."""
    source_post_ids = tuple(item.source_post_id for item in items)
    if len(set(source_post_ids)) != len(source_post_ids):
        raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
    normalized: list[NormalizedPageItem] = []
    for item in items:
        match item:
            case OversizePostInput():
                normalized.append(
                    OversizeRejection(
                        source_post_id=item.source_post_id,
                        canonical_url=str(item.canonical_url),
                        content_hash=item.content_hash,
                        body_bytes=item.body_bytes,
                    )
                )
            case AcceptedPostInput():
                title = _normalize_text(item.title)
                body = _normalize_text(item.body)
                content_hash = compute_content_hash(title, body)
                if content_hash != item.content_hash:
                    raise CollectionError(CollectionErrorCode.INVALID_CONTRACT, 422)
                body_bytes = compute_body_bytes(body)
                if body_bytes > MAX_POST_BYTES:
                    normalized.append(
                        OversizeRejection(
                            source_post_id=item.source_post_id,
                            canonical_url=str(item.canonical_url),
                            content_hash=content_hash,
                            body_bytes=body_bytes,
                        )
                    )
                else:
                    normalized.append(
                        NormalizedPost(
                            source_post_id=item.source_post_id,
                            canonical_url=str(item.canonical_url),
                            title=title,
                            body=body,
                            body_bytes=body_bytes,
                            published_at=require_utc(item.published_at),
                            language=item.language,
                            comments_count=item.comments_count,
                            upvote_or_score=item.upvote_or_score,
                            content_hash=content_hash,
                        )
                    )
            case _:
                assert_never(item)
    return tuple(normalized)
