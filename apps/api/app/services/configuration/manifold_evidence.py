"""Typed privacy and database-time contracts for Manifold evidence."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, ClassVar, Final, Literal
from urllib.parse import quote, unquote, urlsplit

import orjson
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

if TYPE_CHECKING:
    from pathlib import Path

type JsonValue = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)
type JsonDocument = dict[str, JsonValue]

API_ORIGIN: Final[str] = "https://api.manifold.markets"
SITE_ORIGIN: Final[str] = "https://manifold.markets"
MARKETS_ROUTE: Final[str] = "/v0/markets"
COMMENTS_ROUTE: Final[str] = "/v0/comments"
ROUTES: Final[tuple[str, str]] = (COMMENTS_ROUTE, MARKETS_ROUTE)
FIELDS: Final[tuple[str, ...]] = (
    "comment.content.text",
    "comment.contractId",
    "comment.createdTime",
    "comment.id",
    "market.id",
    "market.market_slug",
    "market.neutral_url",
    "market.question",
)
PURPOSE: Final[str] = (
    "personal_noncommercial_prediction_market_monitoring_no_model_training"
)
REVIEWER: Final[str] = "repository-owner-approved-plan-2026-07-27"
DB_NOW_SQL: Final[str] = "SELECT transaction_timestamp()"
MARKET_PATH_SEGMENTS: Final[int] = 2
REQUESTS_PER_MINUTE: Final[int] = 30
PROBE_REQUEST_COUNT: Final[int] = 3
JSON_VALUE: Final[TypeAdapter[JsonValue]] = TypeAdapter(JsonValue)
JSON_DOCUMENT: Final[TypeAdapter[JsonDocument]] = TypeAdapter(JsonDocument)


class ClosedModel(BaseModel):
    """Forbid undeclared fields at every persisted trust boundary."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class OfficialSource(ClosedModel):
    """One official page and its reviewed, non-quoted finding."""

    url: str
    finding: str


class AuthorizationRecord(ClosedModel):
    """Schema-closed, hash-addressed authorization review."""

    schema_name: str = Field(alias="schema")
    retrieved_at: datetime
    recheck_at: datetime
    cadence_anchor_at: datetime
    issuer: str
    reviewer: str
    official_sources: tuple[OfficialSource, ...]
    permitted_methods: tuple[str, ...]
    permitted_routes: tuple[str, ...]
    retained_fields: tuple[str, ...]
    purpose: str
    requests_per_minute: int
    concurrency: int
    no_spend: bool
    sha256: str


class MarketProjection(ClosedModel):
    """Identity-minimized public market projection."""

    id: str
    question: str
    market_slug: str
    neutral_url: str


class CommentContent(ClosedModel):
    """Plain public comment text without provider metadata."""

    text: str


class CommentProjection(ClosedModel):
    """Identity-minimized public comment projection."""

    id: str
    contract_id: str = Field(alias="contractId")
    created_time: int = Field(alias="createdTime")
    content: CommentContent


class LiveProof(ClosedModel):
    """Schema-closed redacted live route proof."""

    schema_name: str = Field(alias="schema")
    prepared_at: datetime
    routes: tuple[str, ...]
    market: MarketProjection
    comment: CommentProjection
    neutral_url_resolves_to_market_id: bool
    request_count: int
    raw_body_persisted: bool
    projection_sha256: str


class CliArgs(ClosedModel):
    """Typed command-line boundary for the three supported operations."""

    command: Literal["probe", "verify", "refresh"]
    output: str | None = None
    database_url_env: str | None = None
    evidence: str | None = None
    live_proof: str | None = None
    json_out: str | None = None


def canonical_bytes(value: JsonValue) -> bytes:
    """Serialize the receipt subset with deterministic RFC-8785-compatible bytes."""
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def record_sha256(document: JsonDocument) -> str:
    """Hash an evidence record while excluding its self-hash field."""
    material = {key: value for key, value in document.items() if key != "sha256"}
    return sha256(canonical_bytes(material)).hexdigest()


def load_record[T: BaseModel](path: Path, model: type[T]) -> T:
    """Parse one schema-closed JSON file into its requested model."""
    return model.model_validate_json(path.read_bytes())


def database_url(env_name: str | None) -> str:
    """Resolve an explicit non-empty database URL environment variable."""
    if not env_name:
        message = "--database-url-env is required"
        raise RuntimeError(message)
    value = os.environ.get(env_name, "").strip()
    if not value:
        message = f"database URL environment variable is empty: {env_name}"
        raise RuntimeError(message)
    return value


def parse_market_url(provider_url: str) -> tuple[str, str]:
    """Discard the creator segment and return the slug and neutral link."""
    parsed = urlsplit(provider_url)
    segments = tuple(unquote(part) for part in parsed.path.split("/") if part)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "manifold.markets"
        or parsed.query
        or parsed.fragment
        or len(segments) != MARKET_PATH_SEGMENTS
        or any(not part or "/" in part for part in segments)
    ):
        message = "provider market URL is outside the reviewed two-segment origin"
        raise ValueError(message)
    market_slug = segments[1]
    neutral_url = f"{SITE_ORIGIN}/market/{quote(market_slug, safe='')}"
    return market_slug, neutral_url


def tiptap_plain_text(value: JsonValue) -> str:
    """Extract only literal text from a TipTap JSON tree."""
    match value:  # noqa: MATCH_OK -- JsonValue variants are type-exhaustive.
        case str() as scalar:
            return scalar
        case list() as items:
            return "".join(tiptap_plain_text(item) for item in items)
        case dict() as mapping:
            own = mapping.get("text")
            nested = mapping.get("content")
            own_text = own if isinstance(own, str) else ""
            nested_text = (
                tiptap_plain_text(nested)
                if isinstance(nested, (list, dict))
                else ""
            )
            separator = (
                "\n" if mapping.get("type") in {"paragraph", "heading"} else ""
            )
            return f"{own_text}{nested_text}{separator}"
        case None:
            return ""
        case int() | float():
            return ""


def is_refresh_current(retrieved_at: datetime, db_now: datetime) -> bool:
    """Apply the inclusive-lower, exclusive-24-hour evidence window."""
    return retrieved_at <= db_now < retrieved_at + timedelta(hours=24)


def is_preparation_usable(prepared_at: datetime, db_now: datetime) -> bool:
    """Apply the inclusive-lower, exclusive-two-hour preparation window."""
    return prepared_at <= db_now < prepared_at + timedelta(hours=2)


def is_activation_before_cutoff(anchor_at: datetime, db_now: datetime) -> bool:
    """Require activation strictly before anchor minus one hour."""
    return db_now < anchor_at - timedelta(hours=1)


def has_recheck_coverage(recheck_at: datetime, anchor_at: datetime) -> bool:
    """Accept recheck coverage through at least anchor plus 31 days."""
    return recheck_at >= anchor_at + timedelta(days=31)


def runtime_preflight_valid(
    effective_at: datetime,
    expires_at: datetime,
    recheck_at: datetime,
    db_now: datetime,
) -> bool:
    """Apply the runtime inclusive lower and strict expiry/recheck bounds."""
    return effective_at <= db_now < expires_at and db_now < recheck_at


def renewal_preserves_epoch(
    old_expires_at: datetime,
    old_recheck_at: datetime,
    db_now: datetime,
    old_scope_sha256: str,
    new_scope_sha256: str,
) -> bool:
    """Preserve an epoch only for early, scope-equivalent renewal."""
    return (
        db_now < old_expires_at
        and db_now < old_recheck_at
        and old_scope_sha256 == new_scope_sha256
    )


def verify_record(
    record: AuthorizationRecord,
    proof: LiveProof,
    db_now: datetime,
) -> tuple[bool, tuple[str, ...]]:
    """Evaluate complete activation proof against one database transaction time."""
    reasons: list[str] = []
    raw = JSON_DOCUMENT.validate_json(record.model_dump_json(by_alias=True))
    if record.sha256 != record_sha256(raw):
        reasons.append("evidence_hash_mismatch")
    if record.retrieved_at > db_now:
        reasons.append("retrieved_at_future")
    if proof.prepared_at > db_now:
        reasons.append("prepared_at_future")
    checks = (
        (record.schema_name == "manifold.authorization.v1", "evidence_schema_invalid"),
        (record.issuer == "Manifold Markets, Inc.", "issuer_invalid"),
        (record.reviewer == REVIEWER, "reviewer_invalid"),
        (record.permitted_methods == ("GET",), "method_scope_invalid"),
        (tuple(sorted(record.permitted_routes)) == ROUTES, "route_scope_invalid"),
        (tuple(sorted(record.retained_fields)) == FIELDS, "field_scope_invalid"),
        (record.purpose == PURPOSE, "purpose_invalid"),
        (record.requests_per_minute == REQUESTS_PER_MINUTE, "rate_invalid"),
        (record.concurrency == 1, "concurrency_invalid"),
        (record.no_spend, "spend_not_prohibited"),
        (tuple(sorted(proof.routes)) == ROUTES, "live_routes_invalid"),
        (proof.market.id == proof.comment.contract_id, "market_id_mismatch"),
        (proof.neutral_url_resolves_to_market_id, "neutral_url_unverified"),
        (proof.request_count == PROBE_REQUEST_COUNT, "request_count_invalid"),
        (not proof.raw_body_persisted, "raw_body_persisted"),
        (is_refresh_current(record.retrieved_at, db_now), "evidence_stale"),
        (is_preparation_usable(proof.prepared_at, db_now), "preparation_stale"),
        (
            is_activation_before_cutoff(record.cadence_anchor_at, db_now),
            "activation_cutoff_reached",
        ),
        (
            has_recheck_coverage(record.recheck_at, record.cadence_anchor_at),
            "recheck_coverage_short",
        ),
        (
            record.recheck_at >= record.retrieved_at + timedelta(days=33),
            "recheck_interval_short",
        ),
    )
    reasons.extend(reason for passed, reason in checks if not passed)
    return not reasons, tuple(reasons)
