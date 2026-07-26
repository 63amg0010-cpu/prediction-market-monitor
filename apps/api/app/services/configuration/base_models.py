"""Frozen source, authorization and budget policy models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_bytes, canonical_sha256
from .errors import invariant

_invariant = invariant


class ImmutableConfigModel(BaseModel):
    """Base for parsed, frozen configuration values."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True,
        extra="forbid",
        populate_by_name=True,
    )

    @property
    def canonical_bytes(self) -> bytes:
        """Return deterministic JSON bytes for this configuration."""
        return canonical_bytes(self)

    @property
    def canonical_sha256(self) -> str:
        """Return the SHA-256 of canonical configuration bytes."""
        return canonical_sha256(self)

    @property
    def canonical_hash(self) -> str:
        """Return the canonical hash under its short compatibility name."""
        return self.canonical_sha256


class AuthorizationStatus(StrEnum):
    """Lifecycle state of an external authorization decision."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"


class SourceState(StrEnum):
    """Fail-closed state of a configured source."""

    PENDING_EVIDENCE = "pending_evidence"
    DISABLED = "disabled"
    ENABLED = "enabled"


class SourceProvider(StrEnum):
    """Supported provider identifiers."""

    REDDIT = "reddit"
    DCINSIDE = "dcinside"
    TOSS = "toss"
    NAVER_FINANCE = "naver_finance"


class AuthorizationEvidence(ImmutableConfigModel):
    """Hash-addressed evidence for an authorization decision."""

    evidence_id: str = Field(min_length=1)
    location: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_at: datetime

    @field_validator("reviewed_at", mode="before")
    @classmethod
    def parse_reviewed_at(cls, value: datetime | str) -> datetime:
        """Parse an evidence review timestamp as UTC."""
        return _parse_datetime(value, "reviewed_at")


class AuthorizationDecision(ImmutableConfigModel):
    """Scoped, expirable and revocable source authorization."""

    status: AuthorizationStatus
    evidence: tuple[AuthorizationEvidence, ...] = ()
    effective_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    permitted_methods: tuple[str, ...] = ()
    permitted_routes: tuple[str, ...] = ()
    permitted_fields: tuple[str, ...] = ()
    purpose: str | None = None

    @field_validator("effective_at", "expires_at", "revoked_at", mode="before")
    @classmethod
    def parse_authorization_time(cls, value: datetime | str | None) -> datetime | None:
        """Parse optional authorization lifecycle timestamps as UTC."""
        return None if value is None else _parse_datetime(value, "authorization_time")

    @field_validator("permitted_methods", mode="before")
    @classmethod
    def normalize_methods(cls, values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
        """Normalize HTTP methods to sorted uppercase values."""
        return tuple(sorted({value.strip().upper() for value in values}))

    @field_validator("permitted_routes", "permitted_fields", mode="before")
    @classmethod
    def normalize_string_set(
        cls,
        values: tuple[str, ...] | list[str],
    ) -> tuple[str, ...]:
        """Normalize reviewed string sets without inventing a value."""
        return tuple(sorted({value.strip() for value in values}))

    @model_validator(mode="after")
    def validate_timing(self) -> AuthorizationDecision:
        """Reject impossible lifecycle windows and incomplete approvals."""
        if (
            self.effective_at is not None
            and self.expires_at is not None
            and self.expires_at <= self.effective_at
        ):
            _invariant(
                "authorization_window_invalid",
                "authorization",
                "expiry must follow effective time",
            )
        if self.status is AuthorizationStatus.REVOKED and self.revoked_at is None:
            _invariant(
                "revocation_time_missing",
                "authorization.revoked_at",
                "revoked decisions require a revocation time",
            )
        if self.status is AuthorizationStatus.APPROVED and not self.permitted_methods:
            _invariant(
                "authorization_method_missing",
                "authorization.permitted_methods",
                "approved decisions require a method",
            )
        return self


class SourceScope(ImmutableConfigModel):
    """Provider scope without an inferred or fabricated route."""

    subreddits: tuple[str, ...] = ()
    gallery_ids: tuple[str, ...] = ()
    reviewed_route: str | None = None

    @field_validator("subreddits", "gallery_ids", mode="before")
    @classmethod
    def normalize_scope_values(
        cls, values: tuple[str, ...] | list[str]
    ) -> tuple[str, ...]:
        """Normalize provider scope identifiers."""
        return tuple(sorted({value.strip() for value in values if value.strip()}))


class SourceLimits(ImmutableConfigModel):
    """Per-source rate and fail-closed collection caps."""

    requests_per_minute: int = Field(gt=0)
    concurrency: int = Field(gt=0)
    max_page_size: int = Field(gt=0, le=20)
    max_accepted_per_run: int = Field(gt=0, le=80)
    max_accepted_per_source_run: int = Field(gt=0, le=20)


class SourceDefinition(ImmutableConfigModel):
    """One reviewed source entry and its authorization gate."""

    source_id: str = Field(min_length=1)
    provider: SourceProvider
    state: SourceState
    enabled: bool
    scope: SourceScope
    authorization: AuthorizationDecision
    limits: SourceLimits


class BudgetPolicy(ImmutableConfigModel):
    """Free-tier thresholds and their fail-closed actions."""

    soft_limit: float
    hard_limit: float
    unit: Literal["fraction_of_free_quota"]
    action_at_soft: Literal["reduce_scope"]
    action_at_hard: Literal["skip_quota"]

    @field_validator("soft_limit", "hard_limit", mode="before")
    @classmethod
    def parse_fraction(cls, value: float | str) -> float:
        """Parse a decimal fraction threshold."""
        return float(value)

    @model_validator(mode="after")
    def validate_thresholds(self) -> BudgetPolicy:
        """Reject thresholds that cannot implement soft and hard stops."""
        if not 0 < self.soft_limit < self.hard_limit <= 1:
            _invariant(
                "budget_threshold_invalid",
                "budget_policy",
                "limits must satisfy 0 < soft < hard <= 1",
            )
        return self


class Exclusivity(ImmutableConfigModel):
    """Mutual-exclusion rule for alternative providers."""

    group_id: str = Field(min_length=1)
    source_ids: tuple[str, ...]
    maximum_enabled: int = Field(gt=0, le=1)

    @field_validator("source_ids", mode="before")
    @classmethod
    def normalize_source_ids(
        cls, values: tuple[str, ...] | list[str]
    ) -> tuple[str, ...]:
        """Normalize the mutually exclusive source identifiers."""
        return tuple(sorted(set(values)))


class ReviewedSources(ImmutableConfigModel):
    """Immutable reviewed source scope and authority configuration."""

    schema_name: Literal["monitor.sources"] = Field(alias="schema")
    version: str
    canonicalization: Literal["json-sort-keys-nfc-v1"]
    scope_version: str
    review_state: Literal["blocked_pending_external_evidence"]
    budget_policy: BudgetPolicy
    exclusivity: Exclusivity
    sources: tuple[SourceDefinition, ...]

    @model_validator(mode="after")
    def validate_sources(self) -> ReviewedSources:
        """Reject duplicate, contradictory or simultaneous source states."""
        source_ids = tuple(source.source_id for source in self.sources)
        if len(source_ids) != len(set(source_ids)):
            _invariant("duplicate_source_id", "sources", "source IDs must be unique")
        if set(self.exclusivity.source_ids) != {"toss", "naver_finance"}:
            _invariant(
                "exclusivity_scope_invalid",
                "exclusivity.source_ids",
                "Toss/Naver must be the exclusive pair",
            )
        enabled = {source.source_id for source in self.sources if source.enabled}
        if (
            len(enabled & set(self.exclusivity.source_ids))
            > self.exclusivity.maximum_enabled
        ):
            _invariant(
                "exclusive_sources_enabled",
                "sources",
                "Toss and Naver cannot both be enabled",
            )
        for source in self.sources:
            if source.enabled and source.state is not SourceState.ENABLED:
                _invariant(
                    "source_state_mismatch",
                    f"sources.{source.source_id}.state",
                    "enabled source must have enabled state",
                )
            if not source.enabled and source.state is SourceState.ENABLED:
                _invariant(
                    "source_state_mismatch",
                    f"sources.{source.source_id}.state",
                    "disabled source cannot have enabled state",
                )
        return self


def _parse_datetime(value: datetime | str, path: str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        _invariant(
            "timestamp_timezone_missing",
            path,
            "timestamp must include timezone",
        )
    return parsed.astimezone(UTC)
