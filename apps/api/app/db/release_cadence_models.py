"""ORM metadata for retained workflow-based cadence evidence."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from .base import Base
from .columns import created_timestamp, sha256_hex, utc_timestamp

SLOT_KEY_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00Z$"
COLLECTION_KEY_PATTERN = r"T(00|03|06|09|12|15|18|21):17:00Z$"
VERIFIER_KEY_PATTERN = r"T[0-9]{2}:(00|15|30|45):00Z$"
KIND_PREFIX = "schedule_kind <>"


class SourceCadenceEpoch(Base):
    """Activation-owned cadence anchor retained across technical rollback."""

    __tablename__: str = "source_cadence_epochs"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "source_id",
            "activation_nonce",
            "cadence_anchor_at",
            name="uq_source_cadence_epoch",
        ),
        UniqueConstraint(
            "id",
            "source_id",
            "activation_nonce",
            name="uq_source_cadence_binding",
        ),
        CheckConstraint(
            "expires_at = cadence_anchor_at + interval '31 days'",
            name="source_cadence_window",
        ),
        CheckConstraint(
            "recheck_at > cadence_anchor_at AND recheck_at <= expires_at",
            name="source_cadence_recheck_window",
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    activation_nonce: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    cadence_anchor_at: Mapped[datetime] = utc_timestamp()
    expires_at: Mapped[datetime] = utc_timestamp()
    recheck_at: Mapped[datetime] = utc_timestamp()
    closed_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    created_at_db: Mapped[datetime] = created_timestamp()


class CadenceEpochContract(Base):
    """Frozen exact two-source identity for one activation cadence epoch."""

    __tablename__: str = "cadence_epoch_contracts"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("epoch_sha256", name="uq_cadence_epoch_hash"),
        CheckConstraint(
            "dcinside_source_id <> manifold_source_id",
            name="cadence_expected_sources_distinct",
        ),
        CheckConstraint(
            "invalidated_at IS NULL OR invalidated_at >= created_at_db",
            name="cadence_invalidation_after_creation",
        ),
    )

    cadence_epoch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_cadence_epochs.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    epoch_sha256: Mapped[str] = sha256_hex()
    dcinside_source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    manifold_source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    binding_sha256: Mapped[str] = sha256_hex()
    scope_sha256: Mapped[str] = sha256_hex()
    window_closes_at: Mapped[datetime] = utc_timestamp()
    invalidated_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    created_at_db: Mapped[datetime] = created_timestamp()


class CadenceWorkflowSlot(Base):
    """One collection or verifier workflow slot, independent of source count."""

    __tablename__: str = "cadence_workflow_slots"
    __table_args__: tuple[SchemaItem, ...] = (
        CheckConstraint(
            "schedule_kind IN ('collection', 'verifier')",
            name="cadence_slot_kind",
        ),
        CheckConstraint(
            f"slot_key ~ '{SLOT_KEY_PATTERN}'",
            name="cadence_slot_key_utc",
        ),
        CheckConstraint(
            f"{KIND_PREFIX} 'collection' OR slot_key ~ '{COLLECTION_KEY_PATTERN}'",
            name="cadence_collection_slot_key",
        ),
        CheckConstraint(
            f"{KIND_PREFIX} 'verifier' OR slot_key ~ '{VERIFIER_KEY_PATTERN}'",
            name="cadence_verifier_slot_key",
        ),
    )

    cadence_epoch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cadence_epoch_contracts.cadence_epoch_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    schedule_kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    slot_key: Mapped[str] = mapped_column(String(20), primary_key=True)
    due_at: Mapped[datetime] = utc_timestamp()
    accepted_attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    created_at_db: Mapped[datetime] = created_timestamp()


class CadenceWorkflowAttempt(Base):
    """Every attempt, including failures, lateness, and CAS duplicates."""

    __tablename__: str = "cadence_workflow_attempts"
    __table_args__: tuple[SchemaItem, ...] = (
        ForeignKeyConstraint(
            ("cadence_epoch_id", "schedule_kind", "slot_key"),
            (
                "cadence_workflow_slots.cadence_epoch_id",
                "cadence_workflow_slots.schedule_kind",
                "cadence_workflow_slots.slot_key",
            ),
            name="fk_cadence_attempt_slot",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "completed_at >= started_at",
            name="cadence_attempt_completion_order",
        ),
        CheckConstraint(
            "NOT accepted OR (eligible AND reason_code = 'accepted')",
            name="cadence_attempt_acceptance",
        ),
        CheckConstraint(
            "NOT accepted OR NOT retry_permitted",
            name="cadence_attempt_retry",
        ),
        Index(
            "uq_cadence_accepted_slot",
            "cadence_epoch_id",
            "schedule_kind",
            "slot_key",
            unique=True,
            postgresql_where=text("accepted"),
        ),
        Index(
            "uq_cadence_workflow_run_attempt",
            "workflow_file",
            "workflow_run_id",
            "workflow_run_attempt",
            unique=True,
        ),
    )

    attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True
    )
    cadence_epoch_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False
    )
    schedule_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(20), nullable=False)
    workflow_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow_file: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    workflow_run_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    cadence_attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    failed_predecessor_attempt_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cadence_workflow_attempts.attempt_id", ondelete="RESTRICT"),
        nullable=True,
    )
    started_at: Mapped[datetime] = utc_timestamp()
    completed_at: Mapped[datetime] = utc_timestamp()
    epoch_sha256: Mapped[str] = sha256_hex()
    binding_sha256: Mapped[str] = sha256_hex()
    scope_sha256: Mapped[str] = sha256_hex()
    eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    accepted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    retry_permitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at_db: Mapped[datetime] = created_timestamp()


class CadenceAttemptSourceReceipt(Base):
    """One successful or failed source projection retained under an attempt."""

    __tablename__: str = "cadence_attempt_source_receipts"

    attempt_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("cadence_workflow_attempts.attempt_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    receipt_sha256: Mapped[str] = sha256_hex()
    created_at_db: Mapped[datetime] = created_timestamp()


__all__ = (
    "CadenceAttemptSourceReceipt",
    "CadenceEpochContract",
    "CadenceWorkflowAttempt",
    "CadenceWorkflowSlot",
    "SourceCadenceEpoch",
)
