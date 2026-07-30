"""Immutable page commits and ordered item outcomes."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
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
from .enum_types import PAGE_ITEM_DISPOSITION, TERMINAL_REASON
from .enums import PageItemDisposition, TerminalReason


class PageCommit(Base):
    """Atomic checkpoint CAS result and replayable response for one page."""

    __tablename__: str = "page_commits"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "run_id", "page_idempotency_key", name="uq_page_commit_idempotency"
        ),
        UniqueConstraint("run_id", "page_ordinal", name="uq_page_commit_ordinal"),
        Index(
            "uq_page_commits_terminal_run",
            "run_id",
            unique=True,
            postgresql_where=text("is_terminal_page"),
        ),
        CheckConstraint("page_ordinal >= 0", name="page_ordinal_nonnegative"),
        CheckConstraint(
            "resulting_checkpoint_revision = expected_checkpoint_revision + 1",
            name="checkpoint_revision_advances_once",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "source_page_item_count = accepted_count + duplicate_count",
                    "+ rejected_count",
                )
            ),
            name="page_item_counts_balance",
        ),
        CheckConstraint(
            "accepted_count >= 0 AND duplicate_count >= 0 AND rejected_count >= 0",
            name="page_counts_nonnegative",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "(is_terminal_page AND terminal_reason IS NOT NULL) OR",
                    "(NOT is_terminal_page AND terminal_reason IS NULL)",
                )
            ),
            name="terminal_reason_matches_flag",
        ),
        CheckConstraint("response_status = 201", name="stored_first_response_created"),
        CheckConstraint(
            "page_fetch_finished_at >= page_fetch_started_at", name="fetch_window_valid"
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    checkpoint_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_checkpoints.id", ondelete="RESTRICT"),
        nullable=False,
    )
    command_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_commands.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_identity_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    page_idempotency_key: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    page_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_checkpoint_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_checkpoint_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_cursor: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_request_hash: Mapped[str] = sha256_hex()
    page_content_hash: Mapped[str] = sha256_hex()
    previous_chain_hash: Mapped[str] = sha256_hex()
    resulting_chain_hash: Mapped[str] = sha256_hex()
    source_page_receipt_sha256: Mapped[str] = sha256_hex()
    source_page_item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_fetch_started_at: Mapped[datetime] = utc_timestamp()
    page_fetch_finished_at: Mapped[datetime] = utc_timestamp()
    is_terminal_page: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    terminal_reason: Mapped[TerminalReason | None] = mapped_column(
        TERMINAL_REASON, nullable=True
    )
    stored_response: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    stored_response_sha256: Mapped[str] = sha256_hex()
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_at: Mapped[datetime] = created_timestamp()


class PageCommitItem(Base):
    """Ordered accepted, duplicate, or oversize-rejected page item result."""

    __tablename__: str = "page_commit_items"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "page_commit_id", "item_ordinal", name="uq_page_commit_item_ordinal"
        ),
        CheckConstraint("item_ordinal >= 0", name="item_ordinal_nonnegative"),
        CheckConstraint(
            sql_expression(
                (
                    "disposition <> 'rejected_oversize' OR",
                    "(post_version_id IS NULL AND rejected_body_bytes IS NOT NULL)",
                )
            ),
            name="oversize_retains_descriptor_only",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    page_commit_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("page_commits.id", ondelete="RESTRICT"),
        nullable=False,
    )
    item_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    disposition: Mapped[PageItemDisposition] = mapped_column(
        PAGE_ITEM_DISPOSITION, nullable=False
    )
    source_post_id: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_content_hash: Mapped[str] = sha256_hex()
    post_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("post_versions.id", ondelete="RESTRICT", use_alter=True),
        nullable=True,
    )
    rejected_body_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
