"""Source checkpoints and version-bound collection runs."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
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

from app.domain.enums import BudgetDecisionStatus, RunStatus, TerminalReason
from app.domain.types import JsonValue

from .base import Base
from .columns import (
    created_timestamp,
    sha256_hex,
    sql_expression,
    utc_timestamp,
    uuid_primary_key,
)
from .enum_types import BUDGET_DECISION_STATUS, RUN_STATUS, TERMINAL_REASON


class SourceCheckpoint(Base):
    """Sole durable cursor and watermark for one source scope."""

    __tablename__: str = "source_checkpoints"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("source_id", "scope_version", name="uq_source_checkpoint"),
        CheckConstraint("revision >= 0", name="checkpoint_revision_nonnegative"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    watermark_published_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    watermark_source_post_id: Mapped[str | None] = mapped_column(
        String(300), nullable=True
    )
    last_completed_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = created_timestamp()


class CollectionRun(Base):
    """One immutable-at-start source attempt with server-owned finalization."""

    __tablename__: str = "collection_runs"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "command_id", "source_id", "attempt", name="uq_run_command_source_attempt"
        ),
        Index(
            "uq_collection_runs_active_source_scope",
            "source_id",
            "scope_version",
            unique=True,
            postgresql_where=text("status IN ('created', 'running')"),
        ),
        CheckConstraint("attempt BETWEEN 1 AND 3", name="run_attempt_range"),
        CheckConstraint(
            sql_expression(
                (
                    "start_checkpoint_revision >= 0 AND next_page_ordinal >= 0",
                    "AND committed_page_count >= 0",
                )
            ),
            name="run_counters_nonnegative",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "(terminal_page_commit_id IS NULL",
                    "AND terminal_page_ordinal IS NULL",
                    "AND terminal_reason IS NULL AND terminal_chain_hash IS NULL",
                    "AND completion_ready_at IS NULL) OR",
                    "(terminal_page_commit_id IS NOT NULL",
                    "AND terminal_page_ordinal IS NOT NULL",
                    "AND terminal_reason IS NOT NULL",
                    "AND terminal_chain_hash IS NOT NULL",
                    "AND completion_ready_at IS NOT NULL)",
                )
            ),
            name="terminal_marker_all_or_nothing",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "status <> 'succeeded' OR",
                    "(terminal_page_commit_id IS NOT NULL",
                    "AND finalized_at IS NOT NULL)",
                )
            ),
            name="success_requires_terminal_marker",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "(page_reservation_id IS NULL AND reserved_page_ordinal IS NULL",
                    "AND page_reservation_expires_at IS NULL) OR",
                    "(page_reservation_id IS NOT NULL",
                    "AND reserved_page_ordinal IS NOT NULL",
                    "AND page_reservation_expires_at IS NOT NULL)",
                )
            ),
            name="page_reservation_all_or_nothing",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "committed_page_count > 0 OR",
                    "(last_page_commit_id IS NULL AND final_page_ordinal IS NULL)",
                )
            ),
            name="zero_commit_has_no_last_page",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "num_nonnulls(skip_authorization_decision_id,",
                    "skip_budget_decision_id) <= 1",
                )
            ),
            name="single_skip_proof",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    command_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_commands.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        RUN_STATUS, nullable=False, server_default=text("'created'")
    )
    start_checkpoint_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    start_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    genesis_chain_hash: Mapped[str] = sha256_hex()
    committed_page_hash_chain: Mapped[str] = sha256_hex()
    next_page_ordinal: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    committed_page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_page_commit_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    final_page_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    final_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminal_page_commit_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "page_commits.id",
            name="fk_runs_terminal_page_commit",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    terminal_page_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terminal_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    terminal_reason: Mapped[TerminalReason | None] = mapped_column(
        TERMINAL_REASON, nullable=True
    )
    terminal_chain_hash: Mapped[str | None] = sha256_hex(nullable=True)
    completion_ready_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    page_reservation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    reserved_page_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_reservation_expires_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    lease_identity_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    authorization_decision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "source_authorization_decisions.id",
            name="fk_runs_claim_authorization",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    authorization_snapshot: Mapped[JsonValue | None] = mapped_column(
        JSONB, nullable=True
    )
    budget_decision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "budget_decisions.id",
            name="fk_runs_claim_budget",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    budget_decision_status: Mapped[BudgetDecisionStatus | None] = mapped_column(
        BUDGET_DECISION_STATUS, nullable=True
    )
    reviewed_page_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reviewed_post_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skip_authorization_decision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "source_authorization_decisions.id",
            name="fk_runs_skip_authorization",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    skip_budget_decision_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "budget_decisions.id",
            name="fk_runs_skip_budget",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    failure_class: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_observed_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    retry_after_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    started_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    heartbeat_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    finalized_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    finished_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    created_at: Mapped[datetime] = created_timestamp()
