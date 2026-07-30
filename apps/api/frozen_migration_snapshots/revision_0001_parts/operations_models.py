"""Scheduled jobs, free-tier budgets, and capability proof records."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from .base import Base
from .columns import (
    created_timestamp,
    sha256_hex,
    sql_expression,
    utc_timestamp,
    uuid_primary_key,
)
from .enum_types import (
    BUDGET_DECISION_STATUS,
    CAPABILITY_KIND,
    JOB_KIND,
    JOB_STATUS,
    PROOF_STATUS,
)
from .enums import (
    BudgetDecisionStatus,
    CapabilityKind,
    JobKind,
    JobStatus,
    ProofStatus,
)
from .types import JsonValue


class ScheduledJobRun(Base):
    """Durable bounded report, retention, or reconciliation execution."""

    __tablename__: str = "scheduled_job_runs"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("idempotency_key", name="uq_scheduled_job_idempotency"),
        CheckConstraint("attempt BETWEEN 1 AND 3", name="scheduled_job_attempt_range"),
        CheckConstraint(
            sql_expression(
                (
                    "status <> 'running' OR",
                    "(lease_hash IS NOT NULL AND started_at IS NOT NULL)",
                )
            ),
            name="running_job_has_lease",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    kind: Mapped[JobKind] = mapped_column(JOB_KIND, nullable=False)
    target_date_seoul: Mapped[date | None] = mapped_column(nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        JOB_STATUS, nullable=False, server_default=text("'queued'")
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    available_at: Mapped[datetime] = utc_timestamp()
    lease_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32), nullable=True)
    started_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    heartbeat_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    finished_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    report_outcome: Mapped[JsonValue | None] = mapped_column(JSONB, nullable=True)
    retention_outcome: Mapped[JsonValue | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = created_timestamp()


class ProviderBudgetRecord(Base):
    """Audited free-tier limit and hard-stop configuration for one period."""

    __tablename__: str = "provider_budget_records"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "provider", "billing_period_start", name="uq_provider_budget_period"
        ),
        CheckConstraint(
            sql_expression(
                (
                    "observed_units >= 0 AND soft_stop_units >= 0",
                    "AND hard_stop_units >= soft_stop_units",
                )
            ),
            name="budget_thresholds_ordered",
        ),
        CheckConstraint("paid_spend_enabled = false", name="paid_spend_disabled"),
        CheckConstraint(
            "billing_period_end > billing_period_start", name="budget_period_valid"
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    billing_period_start: Mapped[datetime] = utc_timestamp()
    billing_period_end: Mapped[datetime] = utc_timestamp()
    observed_units: Mapped[int] = mapped_column(Integer, nullable=False)
    soft_stop_units: Mapped[int] = mapped_column(Integer, nullable=False)
    hard_stop_units: Mapped[int] = mapped_column(Integer, nullable=False)
    paid_spend_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    evidence_sha256: Mapped[str] = sha256_hex()
    evidence_location: Mapped[str] = mapped_column(Text, nullable=False)
    verified_at: Mapped[datetime] = created_timestamp()


class BudgetDecision(Base):
    """Immutable allow, reduction, or hard-stop decision for execution proof."""

    __tablename__: str = "budget_decisions"
    __table_args__: tuple[SchemaItem, ...] = (
        CheckConstraint("observed_units >= 0", name="decision_units_nonnegative"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    budget_record_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("provider_budget_records.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[BudgetDecisionStatus] = mapped_column(
        BUDGET_DECISION_STATUS, nullable=False
    )
    observed_units: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    evidence_sha256: Mapped[str] = sha256_hex()
    decided_at: Mapped[datetime] = created_timestamp()


class CapabilityProofRecord(Base):
    """Persistent fail-closed Windows Codex safety and terms evidence."""

    __tablename__: str = "capability_proof_records"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "kind", "evidence_sha256", name="uq_capability_proof_evidence"
        ),
        CheckConstraint(
            sql_expression(
                (
                    "status <> 'approved' OR",
                    "(effective_at IS NOT NULL AND expires_at IS NOT NULL",
                    "AND expires_at > effective_at AND revoked_at IS NULL)",
                )
            ),
            name="approved_proof_window_valid",
        ),
        CheckConstraint(
            "status <> 'revoked' OR revoked_at IS NOT NULL",
            name="revoked_proof_has_timestamp",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("service_principals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    kind: Mapped[CapabilityKind] = mapped_column(CAPABILITY_KIND, nullable=False)
    status: Mapped[ProofStatus] = mapped_column(PROOF_STATUS, nullable=False)
    evidence_sha256: Mapped[str] = sha256_hex()
    evidence_location: Mapped[str] = mapped_column(Text, nullable=False)
    verified_capabilities: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    effective_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    expires_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    revoked_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    created_at: Mapped[datetime] = created_timestamp()
