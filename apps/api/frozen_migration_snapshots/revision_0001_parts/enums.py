"""Closed persistence vocabularies for Phase 1."""

from enum import StrEnum


class AuthorizationStatus(StrEnum):
    """Lifecycle of an append-only source authorization decision."""

    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"


class SourcePlatform(StrEnum):
    """Reviewed source adapter identities."""

    REDDIT = "reddit"
    DCINSIDE = "dcinside"
    TOSS_SECURITIES = "toss_securities"
    NAVER_FINANCE = "naver_finance"


class Country(StrEnum):
    """MVP source countries."""

    KR = "kr"
    US = "us"


class PrincipalKind(StrEnum):
    """Server-authenticated service principal variants."""

    GITHUB_COLLECTOR = "github_collector"
    GITHUB_VERIFIER = "github_verifier"
    WINDOWS_WORKER = "windows_worker"
    BFF = "bff"
    ADMIN = "admin"


class NoncePurpose(StrEnum):
    """Replay-isolated nonce namespaces."""

    GITHUB_EXCHANGE = "github_exchange"
    WORKER_EXCHANGE = "worker_exchange"
    BFF_EXCHANGE = "bff_exchange"
    SESSION_ROTATION = "session_rotation"


class CommandKind(StrEnum):
    """Collection command origins."""

    SCHEDULED = "scheduled"
    MANUAL = "manual"
    RECOVERY = "recovery"


class CommandStatus(StrEnum):
    """Exhaustive collection command lifecycle."""

    QUEUED = "queued"
    DISPATCH_RESERVED = "dispatch_reserved"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    STALE_ABANDONED = "stale_abandoned"


class RunStatus(StrEnum):
    """Exhaustive per-source collection run lifecycle."""

    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    SKIPPED_POLICY = "skipped_policy"
    SKIPPED_QUOTA = "skipped_quota"
    STALE_ABANDONED = "stale_abandoned"


class TerminalReason(StrEnum):
    """Server-verifiable reasons that seal a page stream."""

    SOURCE_EXHAUSTED = "source_exhausted"
    REVIEWED_PAGE_CAP = "reviewed_page_cap"
    REVIEWED_POST_CAP = "reviewed_post_cap"


class PageItemDisposition(StrEnum):
    """Ordered page item persistence outcomes."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED_OVERSIZE = "rejected_oversize"


class PostVersionReason(StrEnum):
    """Reason an immutable post revision was captured."""

    FIRST_SEEN = "first_seen"
    SOURCE_EDIT = "source_edit"
    NORMALIZATION_CORRECTION = "normalization_correction"


class QueueStatus(StrEnum):
    """Version-bound analysis queue lifecycle."""

    PENDING = "pending"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    BLOCKED_CAPABILITY = "blocked_capability"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


class AnalysisState(StrEnum):
    """Retained analysis validity states used by reports."""

    VALID = "valid"
    PENDING = "pending"
    BLOCKED_CAPABILITY = "blocked_capability"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    INVALID_OUTPUT = "invalid_output"


class Sentiment(StrEnum):
    """Sentiment variants allowed for valid relevant analyses."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class ReportRole(StrEnum):
    """Primary and comparison report windows."""

    PRIMARY = "primary"
    COMPARISON = "comparison"


class ManifestItemKind(StrEnum):
    """Value-slice variants within a report input manifest."""

    RECORD = "record"
    SOURCE_COVERAGE = "source_coverage"


class ManifestCodec(StrEnum):
    """Deterministic manifest compression codecs."""

    GZIP = "gzip"


class ReportStatus(StrEnum):
    """Truthful daily report completeness."""

    COMPLETE = "complete"
    PARTIAL = "partial"


class TombstoneEntityKind(StrEnum):
    """Provenance entities eligible for tombstone switching."""

    POST_VERSION = "post_version"
    ANALYSIS = "analysis"
    MATCH = "match"
    ENGAGEMENT = "engagement"
    SOURCE_MANIFEST = "source_manifest"


class TombstoneDeletionReason(StrEnum):
    """Auditable deletion reasons for retained provenance."""

    RAW_RETENTION_EXPIRED = "raw_retention_expired"
    SOURCE_CLEANUP = "source_cleanup"


class VerificationStatus(StrEnum):
    """Expected verifier slot outcomes."""

    PASSED = "passed"
    FAILED = "failed"
    MISSING = "missing"


class JobKind(StrEnum):
    """Bounded daily control-plane jobs."""

    DAILY_REPORT = "daily_report"
    RETENTION = "retention"
    RECONCILIATION = "reconciliation"


class JobStatus(StrEnum):
    """Durable scheduled job lifecycle."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"


class BudgetDecisionStatus(StrEnum):
    """Free-tier budget enforcement outcomes."""

    ALLOW = "allow"
    SOFT_LIMIT = "soft_limit"
    HARD_STOP = "hard_stop"


class CapabilityKind(StrEnum):
    """Windows Codex capabilities requiring persistent proof."""

    AUTOMATION_TERMS = "automation_terms"
    JSON_SCHEMA = "json_schema"
    NO_TOOLS_SANDBOX = "no_tools_sandbox"


class ProofStatus(StrEnum):
    """Capability proof lifecycle."""

    APPROVED = "approved"
    FAILED = "failed"
    REVOKED = "revoked"
    EXPIRED = "expired"
