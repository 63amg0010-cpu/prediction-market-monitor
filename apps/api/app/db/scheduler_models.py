"""Scheduler slots, collection commands, and completion receipts."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from app.domain.enums import CommandKind, CommandStatus

from .base import Base
from .columns import (
    created_timestamp,
    sha256_hex,
    sql_expression,
    utc_timestamp,
    uuid_primary_key,
)
from .enum_types import COMMAND_KIND, COMMAND_STATUS


class SchedulerCursor(Base):
    """Last collection grid slot materialized for one immutable scope."""

    __tablename__: str = "scheduler_cursors"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("scope_version", name="uq_scheduler_scope"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    last_materialized_slot_utc: Mapped[datetime | None] = utc_timestamp(nullable=True)
    updated_at: Mapped[datetime] = created_timestamp()


class CollectionSlot(Base):
    """One materialized minute-17 UTC collection grid slot."""

    __tablename__: str = "collection_slots"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "scope_version", "due_slot_utc", name="uq_collection_scope_due_slot"
        ),
        UniqueConstraint("scheduled_key", name="uq_collection_scheduled_key"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    due_slot_utc: Mapped[datetime] = utc_timestamp()
    scheduled_key: Mapped[str] = mapped_column(String(180), nullable=False)
    materialized_at: Mapped[datetime] = created_timestamp()


class CollectionCommand(Base):
    """Durable dispatch, lease, retry, and aggregate collection state."""

    __tablename__: str = "collection_commands"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("idempotency_key", name="uq_collection_command_idempotency"),
        CheckConstraint("attempt BETWEEN 1 AND 3", name="command_attempt_range"),
        CheckConstraint(
            sql_expression(
                (
                    "reservation_nonce_hash IS NULL OR",
                    "octet_length(reservation_nonce_hash) = 32",
                )
            ),
            name="reservation_nonce_sha256",
        ),
        CheckConstraint(
            "lease_hash IS NULL OR octet_length(lease_hash) = 32",
            name="command_lease_sha256",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "status <> 'running' OR",
                    "(claimed_at IS NOT NULL AND lease_hash IS NOT NULL)",
                )
            ),
            name="running_has_claim_lease",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    slot_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_slots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_set_hash: Mapped[str] = sha256_hex()
    kind: Mapped[CommandKind] = mapped_column(COMMAND_KIND, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(220), nullable=False)
    status: Mapped[CommandStatus] = mapped_column(
        COMMAND_STATUS, nullable=False, server_default=text("'queued'")
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    available_at: Mapped[datetime] = utc_timestamp()
    reservation_started_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    reservation_nonce_hash: Mapped[bytes | None] = mapped_column(
        LargeBinary(32), nullable=True
    )
    dispatched_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    claimed_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    heartbeat_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    completed_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    lease_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32), nullable=True)
    github_run_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    github_run_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = created_timestamp()


class CommandCompletion(Base):
    """Immutable command-finalization idempotency receipt."""

    __tablename__: str = "command_completions"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "command_id", "completion_idempotency_key", name="uq_command_completion_key"
        ),
        CheckConstraint("attempt BETWEEN 1 AND 3", name="completion_attempt_range"),
        CheckConstraint("response_status = 200", name="completion_success_response"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    command_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_commands.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    completion_idempotency_key: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    request_hash: Mapped[str] = sha256_hex()
    response_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    response_sha256: Mapped[str] = sha256_hex()
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = created_timestamp()
