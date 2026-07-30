"""Author-free Manifold JSON boundary contracts."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import ClassVar, Final, override
from urllib.parse import quote, unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, ValidationError

MAX_MANIFOLD_RESPONSE_BYTES: Final = 262_144
MANIFOLD_ORIGIN: Final = "https://manifold.markets"
_EXPECTED_PATH_SEGMENTS: Final = 3
_PERCENT_ESCAPE: Final = re.compile(r"%(?![0-9A-Fa-f]{2})")


class ManifoldContractFailureCode(StrEnum):
    """Stable failures that never include provider input."""

    RESPONSE_OVERSIZE = "manifold_response_oversize"
    MARKET_WIRE_INVALID = "manifold_market_wire_invalid"
    MARKET_URL_INVALID = "manifold_market_url_invalid"
    COMMENT_WIRE_INVALID = "manifold_comment_wire_invalid"


class ManifoldContractError(Exception):
    """Provider-input-free boundary error for one malformed response."""

    code: ManifoldContractFailureCode

    def __init__(self, code: ManifoldContractFailureCode) -> None:
        """Store only a stable failure code, never provider input."""
        super().__init__(code)
        self.code = code

    @override
    def __str__(self) -> str:
        """Return only the stable contract failure code."""
        return self.code.value


class _WireModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="ignore")


class _MarketWire(_WireModel):
    """Transient allowlisted market response fields."""

    id: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=20_000)
    url: str = Field(min_length=1, max_length=2_048)


class ManifoldMarket(BaseModel):
    """Market fields permitted beyond the provider boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=20_000)
    market_slug: str = Field(min_length=1, max_length=2_048)
    neutral_url: str = Field(pattern=r"^https://manifold\.markets/market/")


class ManifoldCommentWire(_WireModel):
    """Only comment fields permitted to reach text normalization."""

    id: str = Field(min_length=1, max_length=300)
    contract_id: str = Field(min_length=1, max_length=300, alias="contractId")
    created_time: int = Field(strict=True, ge=0, alias="createdTime")
    content: JsonValue


class _MarketList(RootModel[tuple[_MarketWire, ...]]):
    pass


class _CommentList(RootModel[tuple[ManifoldCommentWire, ...]]):
    pass


def parse_manifold_markets_json(payload: bytes) -> tuple[ManifoldMarket, ...]:
    """Parse one bounded market-list response into author-free values."""
    _require_response_within_cap(payload)
    try:
        wires = _MarketList.model_validate_json(payload).root
    except ValidationError:
        raise ManifoldContractError(
            ManifoldContractFailureCode.MARKET_WIRE_INVALID
        ) from None
    return tuple(_market_from_wire(wire) for wire in wires)


def parse_manifold_comments_json(
    payload: bytes,
) -> tuple[ManifoldCommentWire, ...]:
    """Parse one bounded comment-list response while dropping unknown fields."""
    _require_response_within_cap(payload)
    try:
        return _CommentList.model_validate_json(payload).root
    except ValidationError:
        raise ManifoldContractError(
            ManifoldContractFailureCode.COMMENT_WIRE_INVALID
        ) from None


def parse_manifold_market_json(payload: bytes) -> ManifoldMarket:
    """Parse one bounded market response into an author-free market value."""
    _require_response_within_cap(payload)
    try:
        wire = _MarketWire.model_validate_json(payload)
    except ValidationError:
        raise ManifoldContractError(
            ManifoldContractFailureCode.MARKET_WIRE_INVALID
        ) from None
    return _market_from_wire(wire)


def parse_manifold_comment_json(payload: bytes) -> ManifoldCommentWire:
    """Parse one bounded comment while discarding all unknown provider fields."""
    _require_response_within_cap(payload)
    try:
        return ManifoldCommentWire.model_validate_json(payload)
    except ValidationError:
        raise ManifoldContractError(
            ManifoldContractFailureCode.COMMENT_WIRE_INVALID
        ) from None


def _require_response_within_cap(payload: bytes) -> None:
    if len(payload) > MAX_MANIFOLD_RESPONSE_BYTES:
        raise ManifoldContractError(ManifoldContractFailureCode.RESPONSE_OVERSIZE)


def _market_from_wire(wire: _MarketWire) -> ManifoldMarket:
    market_slug = _market_slug_from_url(wire.url)
    return ManifoldMarket(
        id=wire.id,
        question=wire.question,
        market_slug=market_slug,
        neutral_url=f"{MANIFOLD_ORIGIN}/market/{quote(market_slug, safe='-._~')}",
    )


def _market_slug_from_url(value: str) -> str:
    if _PERCENT_ESCAPE.search(value) is not None:
        raise ManifoldContractError(ManifoldContractFailureCode.MARKET_URL_INVALID)
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ManifoldContractError(
            ManifoldContractFailureCode.MARKET_URL_INVALID
        ) from None
    decoded_path = unquote(parsed.path)
    segments = decoded_path.split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "manifold.markets"
        or parsed.query
        or parsed.fragment
        or len(segments) != _EXPECTED_PATH_SEGMENTS
        or segments[0] != ""
        or not segments[1]
        or not segments[2]
    ):
        raise ManifoldContractError(ManifoldContractFailureCode.MARKET_URL_INVALID)
    return segments[2]
