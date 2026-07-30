"""Typed values shared by every source adapter."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import ClassVar, Literal, override
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import AuthorizationStatus, SourcePlatform


class AdapterModel(BaseModel):
    """Frozen adapter boundary value."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class HttpMethod(StrEnum):
    """HTTP methods that a source authorization may permit."""

    GET = "GET"


class BlockedKind(StrEnum):
    """Fail-closed preflight outcomes."""

    BLOCKED_POLICY = "blocked_policy"
    BLOCKED_AUTHORIZATION = "blocked_authorization"


class PageTermination(StrEnum):
    """Provider-page continuation decisions."""

    CONTINUE = "continue"
    SOURCE_EXHAUSTED = "source_exhausted"
    REVIEWED_POST_CAP = "reviewed_post_cap"
    REVIEWED_BYTE_CAP = "reviewed_byte_cap"
    RATE_LIMIT_PAUSE = "rate_limit_pause"


class SourceAuthorizationDecision(AdapterModel):
    """Caller-supplied current snapshot of an append-only source approval."""

    decision_id: UUID
    source: SourcePlatform
    status: AuthorizationStatus
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_location: str = Field(min_length=1)
    issuer: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    permitted_methods: frozenset[HttpMethod]
    permitted_routes: frozenset[str]
    permitted_fields: frozenset[str]
    permitted_subreddits: frozenset[str]
    purpose: str = Field(min_length=1)
    requests_per_minute: int = Field(gt=0)
    concurrency: int = Field(gt=0)
    effective_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


class PreflightContext(AdapterModel):
    """Fresh authority and exclusivity state supplied for one operation."""

    authorization: SourceAuthorizationDecision | None
    checked_at: datetime
    enabled_finance_sources: frozenset[SourcePlatform] = frozenset()


class PreflightReady(AdapterModel):
    """Proof that an adapter operation may proceed."""

    decision_id: UUID


class PreflightBlocked(AdapterModel):
    """Explicit policy or authorization denial."""

    kind: BlockedKind
    code: str = Field(min_length=1)


type PreflightResult = PreflightReady | PreflightBlocked


class NormalizedPost(AdapterModel):
    """Author-free full accepted post representation."""

    source: SourcePlatform
    source_post_id: str = Field(min_length=1, max_length=300)
    canonical_url: str = Field(pattern=r"^https://")
    title: str
    body: str
    published_at: datetime
    language: Literal["ko", "en"]
    comments_count: int | None = Field(default=None, ge=0)
    upvote_or_score: int | None = None
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class RejectedOversize(AdapterModel):
    """Metadata retained when original content exceeds the reviewed byte cap."""

    source: SourcePlatform
    source_post_id: str = Field(min_length=1, max_length=300)
    canonical_url: str = Field(pattern=r"^https://")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=262_144)
    reason: Literal["rejected_oversize"] = "rejected_oversize"


type NormalizedItem = NormalizedPost | RejectedOversize


class RateLimitSnapshot(AdapterModel):
    """Adaptive Reddit rate-limit values observed on one response."""

    used: Decimal | None
    remaining: Decimal | None
    reset_after_seconds: Decimal | None
    retry_after_seconds: Decimal | None


class AdapterPage(AdapterModel):
    """One normalized provider page with its checkpoint decision."""

    items: tuple[NormalizedItem, ...]
    next_cursor: str | None
    accepted_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    rate_limit: RateLimitSnapshot
    termination: PageTermination


class BlockedFetchRequest(AdapterModel):
    """Fetch request accepted only to return a typed denial."""

    preflight: PreflightContext


class SourceBlockedError(Exception):
    """Safe error raised before any forbidden source request."""

    source: SourcePlatform
    kind: BlockedKind
    code: str

    def __init__(self, source: SourcePlatform, denial: PreflightBlocked) -> None:
        """Capture only the provider identity and typed denial."""
        super().__init__(source, denial.kind, denial.code)
        self.source = source
        self.kind = denial.kind
        self.code = denial.code

    @override
    def __str__(self) -> str:
        """Return the body-free denial suitable for persistence."""
        return f"{self.source.value}: {self.kind.value}: {self.code}"
