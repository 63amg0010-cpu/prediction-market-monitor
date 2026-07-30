"""TipTap-to-text normalization for author-free Manifold comments."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, JsonValue, RootModel, ValidationError

from app.collection.normalizer import compute_body_bytes, compute_content_hash
from app.domain.enums import Country

if TYPE_CHECKING:
    from .manifold_contracts import ManifoldCommentWire, ManifoldMarket

MAX_MANIFOLD_BODY_BYTES: Final = 262_144


class _JsonValueDocument(RootModel[JsonValue]):
    pass


class ManifoldCommentSkipCode(StrEnum):
    """Stable body-free outcomes for a non-normalizable comment."""

    CONTRACT_ID_MISMATCH = "manifold_contract_id_mismatch"
    CREATED_TIME_INVALID = "manifold_created_time_invalid"
    EMPTY = "manifold_comment_empty"
    DELETED = "manifold_comment_deleted"
    SYSTEM = "manifold_comment_system"
    UNPARSEABLE = "manifold_comment_unparseable"


class _NormalizationModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ManifoldNormalizedComment(_NormalizationModel):
    """Safe normalized content ready for the later Manifold adapter."""

    source_post_id: str
    canonical_url: str
    title: str
    body: str
    published_at: datetime
    country: Literal[Country.US]
    language: Literal["en"]
    content_hash: str
    size_bytes: int


class ManifoldRejectedOversize(_NormalizationModel):
    """Body-free descriptor for a comment exceeding the reviewed cap."""

    source_post_id: str
    canonical_url: str
    content_hash: str
    size_bytes: int
    reason: Literal["rejected_oversize"] = "rejected_oversize"


class ManifoldSkippedComment(_NormalizationModel):
    """Body-free stable outcome for an unusable comment."""

    source_post_id: str
    canonical_url: str
    code: ManifoldCommentSkipCode


type ManifoldNormalizationResult = (
    ManifoldNormalizedComment | ManifoldRejectedOversize | ManifoldSkippedComment
)


def normalize_manifold_comment(
    market: ManifoldMarket, comment: ManifoldCommentWire
) -> ManifoldNormalizationResult:
    """Normalize one comment without exposing provider identity metadata."""
    if comment.contract_id != market.id:
        return _skip(comment, market, ManifoldCommentSkipCode.CONTRACT_ID_MISMATCH)
    body = tiptap_to_nfc_text(comment.content)
    if isinstance(body, ManifoldCommentSkipCode):
        return _skip(comment, market, body)
    if not body:
        return _skip(comment, market, ManifoldCommentSkipCode.EMPTY)
    try:
        published_at = datetime.fromtimestamp(comment.created_time / 1000, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return _skip(comment, market, ManifoldCommentSkipCode.CREATED_TIME_INVALID)
    title = unicodedata.normalize("NFC", market.question)
    content_hash = compute_content_hash(title, body)
    size_bytes = compute_body_bytes(body)
    if size_bytes > MAX_MANIFOLD_BODY_BYTES:
        return ManifoldRejectedOversize(
            source_post_id=comment.id,
            canonical_url=market.neutral_url,
            content_hash=content_hash,
            size_bytes=size_bytes,
        )
    return ManifoldNormalizedComment(
        source_post_id=comment.id,
        canonical_url=market.neutral_url,
        title=title,
        body=body,
        published_at=published_at,
        country=Country.US,
        language="en",
        content_hash=content_hash,
        size_bytes=size_bytes,
    )


def tiptap_to_nfc_text(content: JsonValue) -> str | ManifoldCommentSkipCode:
    """Render the reviewed TipTap subset into deterministic NFC plain text."""
    document = _decode_content(content)
    if isinstance(document, ManifoldCommentSkipCode):
        return document
    match document:
        case {"type": "deleted"}:
            return ManifoldCommentSkipCode.DELETED
        case {"type": "system"}:
            return ManifoldCommentSkipCode.SYSTEM
        case {"type": "doc"} as root:
            rendered = _render_children(root, "\n\n")
            if rendered is None:
                return ManifoldCommentSkipCode.UNPARSEABLE
            return unicodedata.normalize("NFC", rendered)
        case _:
            return ManifoldCommentSkipCode.UNPARSEABLE


def _decode_content(content: JsonValue) -> JsonValue | ManifoldCommentSkipCode:
    match content:
        case str() as serialized:
            try:
                return _JsonValueDocument.model_validate_json(serialized).root
            except ValidationError:
                return ManifoldCommentSkipCode.UNPARSEABLE
        case _:
            return content


def _render_children(node: dict[str, JsonValue], separator: str) -> str | None:
    match node.get("content", []):
        case list() as children:
            rendered = tuple(_render_node(child) for child in children)
        case _:
            return None
    if any(value is None for value in rendered):
        return None
    return separator.join(value for value in rendered if value is not None)


def _render_node(node: JsonValue) -> str | None:
    match node:
        case {"type": "text", "text": str() as text}:
            return text
        case {"type": "hardBreak"}:
            return "\n"
        case {"type": ("paragraph" | "codeBlock")} as block:
            return _render_children(block, "")
        case {
            "type": (
                "blockquote" | "bulletList" | "orderedList" | "listItem"
            )
        } as block:
            return _render_children(block, "\n")
        case _:
            return None


def _skip(
    comment: ManifoldCommentWire,
    market: ManifoldMarket,
    code: ManifoldCommentSkipCode,
) -> ManifoldSkippedComment:
    return ManifoldSkippedComment(
        source_post_id=comment.id,
        canonical_url=market.neutral_url,
        code=code,
    )
