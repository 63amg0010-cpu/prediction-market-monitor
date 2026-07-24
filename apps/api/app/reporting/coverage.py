"""Source-coverage status and exact ratio models."""

from decimal import Decimal, localcontext
from enum import StrEnum
from uuid import UUID

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from app.domain.enums import Country, ReportRole, SourcePlatform

from .inputs import ImmutableReportModel, SafeCount, Sha256Hex, UtcTimestamp


class CollectionStatus(StrEnum):
    """Truthful collection outcome for one source and report role."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    MISSING = "missing"
    SKIPPED_POLICY = "skipped_policy"
    SKIPPED_QUOTA = "skipped_quota"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    UNAUTHORIZED = "unauthorized"


def ratio_decimal(numerator: int, denominator: int) -> str | None:
    """Return a deterministic non-exponent decimal or null for zero denominator."""
    if denominator == 0:
        return None
    with localcontext() as decimal_context:
        decimal_context.prec = 28
        text = format(Decimal(numerator) / Decimal(denominator), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


class SourceCoverage(ImmutableReportModel):
    """Value-complete source status, counts, cutoffs, and event clocks."""

    role: ReportRole
    source_id: UUID
    country: Country
    platform: SourcePlatform
    community: str
    expected: bool
    enabled: bool
    collection_status: CollectionStatus
    expected_run_count: SafeCount
    successful_run_count: SafeCount
    failed_run_count: SafeCount
    skipped_run_count: SafeCount
    candidate_count: SafeCount
    valid_analysis_count: SafeCount
    pending_count: SafeCount
    relevant_count: SafeCount
    cutoff_publication_sequence: SafeCount | None
    cutoff_publication_manifest_id: UUID | None
    cutoff_publication_manifest_hash: Sha256Hex | None
    latest_successful_run_started_at: UtcTimestamp | None
    latest_successful_run_finished_at: UtcTimestamp | None
    latest_publication_committed_at: UtcTimestamp | None
    latest_attempt_finished_at: UtcTimestamp | None
    status_observed_at: UtcTimestamp | None
    coverage_numerator: SafeCount
    coverage_denominator: SafeCount
    coverage_decimal: str | None

    @model_validator(mode="after")
    def validate_counts(self) -> "SourceCoverage":
        """Require record-derived counts, cutoff grouping, and exact coverage."""
        counts_valid = (
            self.valid_analysis_count <= self.candidate_count
            and self.relevant_count <= self.valid_analysis_count
            and self.pending_count == self.candidate_count - self.valid_analysis_count
            and self.coverage_numerator == self.valid_analysis_count
            and self.coverage_denominator == self.candidate_count
        )
        expected_decimal = ratio_decimal(
            self.coverage_numerator,
            self.coverage_denominator,
        )
        if not counts_valid or self.coverage_decimal != expected_decimal:
            error_code = "coverage_counts_mismatch"
            raise PydanticCustomError(
                error_code,
                "coverage counts and decimal must be exact",
            )
        cutoff = (
            self.cutoff_publication_sequence,
            self.cutoff_publication_manifest_id,
            self.cutoff_publication_manifest_hash,
        )
        cutoff_present = tuple(value is not None for value in cutoff)
        if any(cutoff_present) and not all(cutoff_present):
            error_code = "coverage_cutoff_incomplete"
            raise PydanticCustomError(
                error_code,
                "publication cutoff provenance is all-or-nothing",
            )
        terminal_count = (
            self.successful_run_count + self.failed_run_count + self.skipped_run_count
        )
        if terminal_count > self.expected_run_count:
            error_code = "coverage_run_count_exceeded"
            raise PydanticCustomError(
                error_code,
                "terminal run counts cannot exceed expected runs",
            )
        allowed = _allowed_statuses(self)
        if self.collection_status not in allowed:
            error_code = "coverage_run_status_mismatch"
            raise PydanticCustomError(
                error_code,
                "collection status must be derived from retained run counts",
            )
        return self


def _allowed_statuses(coverage: SourceCoverage) -> frozenset[CollectionStatus]:
    terminal_runs = (
        coverage.successful_run_count
        + coverage.failed_run_count
        + coverage.skipped_run_count
    )
    if coverage.expected and not coverage.enabled:
        return frozenset({CollectionStatus.UNAUTHORIZED})
    if (
        coverage.expected_run_count > 0
        and coverage.successful_run_count == coverage.expected_run_count
    ):
        return frozenset({CollectionStatus.COMPLETE})
    if terminal_runs == 0:
        return frozenset({CollectionStatus.MISSING})
    if coverage.successful_run_count > 0:
        return frozenset({CollectionStatus.PARTIAL})
    if coverage.failed_run_count > 0:
        return frozenset(
            {
                CollectionStatus.FAILED_TERMINAL,
                CollectionStatus.FAILED_RETRYABLE,
            }
        )
    return frozenset(
        {
            CollectionStatus.SKIPPED_POLICY,
            CollectionStatus.SKIPPED_QUOTA,
            CollectionStatus.PARTIAL,
        }
    )


class CoverageRunFacts(ImmutableReportModel):
    """Terminal run facts used to derive one collection status."""

    expected: bool
    authorization_required: bool
    authorization_active: bool
    expected_runs: SafeCount
    successful_runs: SafeCount
    skipped_policy_runs: SafeCount
    skipped_quota_runs: SafeCount
    failed_terminal_runs: SafeCount
    failed_retryable_or_abandoned_runs: SafeCount


def derive_collection_status(facts: CoverageRunFacts) -> CollectionStatus:
    """Apply the normative collection-status precedence."""
    terminal_count = (
        facts.successful_runs
        + facts.skipped_policy_runs
        + facts.skipped_quota_runs
        + facts.failed_terminal_runs
        + facts.failed_retryable_or_abandoned_runs
    )
    unauthorized = (
        facts.expected
        and facts.authorization_required
        and not facts.authorization_active
    )
    precedence = (
        (unauthorized, CollectionStatus.UNAUTHORIZED),
        (
            facts.expected_runs > 0 and facts.successful_runs == facts.expected_runs,
            CollectionStatus.COMPLETE,
        ),
        (terminal_count == 0, CollectionStatus.MISSING),
        (facts.successful_runs > 0, CollectionStatus.PARTIAL),
        (
            facts.skipped_policy_runs == terminal_count,
            CollectionStatus.SKIPPED_POLICY,
        ),
        (
            facts.skipped_quota_runs == terminal_count,
            CollectionStatus.SKIPPED_QUOTA,
        ),
        (facts.failed_terminal_runs > 0, CollectionStatus.FAILED_TERMINAL),
        (
            facts.failed_retryable_or_abandoned_runs > 0,
            CollectionStatus.FAILED_RETRYABLE,
        ),
    )
    return next(
        (status for matches, status in precedence if matches),
        CollectionStatus.PARTIAL,
    )
