"""Independent verifier cursors and immutable expected-slot observations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from app.domain.enums import Country, VerificationStatus

from .base import Base
from .columns import (
    created_timestamp,
    sha256_hex,
    sql_expression,
    utc_timestamp,
    uuid_primary_key,
)
from .enum_types import COUNTRY, VERIFICATION_STATUS


class VerificationCursor(Base):
    """Last expected fifteen-minute verifier slot for one scope."""

    __tablename__: str = "verification_cursors"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("scope_version", name="uq_verification_scope"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    last_materialized_slot_utc: Mapped[datetime | None] = utc_timestamp(nullable=True)
    updated_at: Mapped[datetime] = created_timestamp()


class VerificationSnapshotRecord(Base):
    """Canonical database-time fact issued to one verifier action."""

    __tablename__: str = "verification_snapshots"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("snapshot_checksum", name="uq_verification_snapshot_hash"),
        CheckConstraint(
            "octet_length(canonical_payload) > 0",
            name="verification_snapshot_payload_nonempty",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    published_at: Mapped[datetime] = utc_timestamp()
    snapshot_checksum: Mapped[str] = sha256_hex()
    canonical_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class VerificationSnapshotSource(Base):
    """Free-text-free source facts retained by one verifier snapshot."""

    __tablename__: str = "verification_snapshot_sources"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "snapshot_id", "source_id", name="uq_verification_snapshot_source"
        ),
        CheckConstraint(
            sql_expression(
                (
                    "(latest_successful_run_id IS NULL",
                    "AND latest_successful_run_finished_at IS NULL) OR",
                    "(latest_successful_run_id IS NOT NULL",
                    "AND latest_successful_run_finished_at IS NOT NULL)",
                )
            ),
            name="verification_snapshot_run_all_or_nothing",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "(visible_publication_manifest_id IS NULL",
                    "AND visible_publication_sequence IS NULL",
                    "AND publication_first_visible_at IS NULL) OR",
                    "(visible_publication_manifest_id IS NOT NULL",
                    "AND visible_publication_sequence > 0",
                    "AND publication_first_visible_at IS NOT NULL)",
                )
            ),
            name="verification_snapshot_manifest_all_or_nothing",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "publication_first_visible_at IS NULL OR",
                    "publication_first_visible_at",
                    ">= latest_successful_run_finished_at",
                )
            ),
            name="verification_snapshot_visibility_after_run",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "verification_snapshots.id",
            name="fk_verification_snapshot_source_snapshot",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    country: Mapped[Country] = mapped_column(COUNTRY, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latest_successful_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    latest_successful_run_finished_at: Mapped[datetime | None] = utc_timestamp(
        nullable=True
    )
    visible_publication_manifest_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    visible_publication_sequence: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    publication_first_visible_at: Mapped[datetime | None] = utc_timestamp(nullable=True)


class VerificationSnapshotUse(Base):
    """One immutable consumption of a snapshot for an expected verifier slot."""

    __tablename__: str = "verification_snapshot_uses"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("snapshot_id", name="uq_verification_snapshot_use"),
        UniqueConstraint(
            "scope_version",
            "expected_slot_utc",
            name="uq_verification_snapshot_use_slot",
        ),
        CheckConstraint(
            "action_started_at >= expected_slot_utc",
            name="verification_snapshot_use_action_after_slot",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "verification_snapshots.id",
            name="fk_verification_snapshot_use_snapshot",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    expected_slot_utc: Mapped[datetime] = utc_timestamp()
    action_started_at: Mapped[datetime] = utc_timestamp()
    consumed_at: Mapped[datetime] = utc_timestamp()


class VerificationObservation(Base):
    """Immutable S/C/P evidence for one expected source observation."""

    __tablename__: str = "verification_observations"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "scope_version",
            "expected_slot_utc",
            "source_id",
            name="uq_verification_expected_source_slot",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "scheduler_latency_seconds >= 0",
                    "AND (collection_recency_seconds IS NULL",
                    "OR collection_recency_seconds >= 0)",
                    "AND (publication_latency_seconds IS NULL",
                    "OR publication_latency_seconds >= 0)",
                )
            ),
            name="verification_latencies_nonnegative",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "status <> 'passed' OR",
                    "(collection_recency_seconds <= 10800",
                    "AND publication_latency_seconds BETWEEN 0 AND 10800)",
                )
            ),
            name="verification_pass_within_three_hours",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    expected_slot_utc: Mapped[datetime] = utc_timestamp()
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "verification_snapshots.id",
            name="fk_verification_observations_snapshot",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    snapshot_published_at: Mapped[datetime] = utc_timestamp()
    latest_successful_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="RESTRICT"),
        nullable=True,
    )
    visible_publication_manifest_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_run_publication_manifests.id", ondelete="RESTRICT"),
        nullable=True,
    )
    visible_publication_sequence: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    action_started_at: Mapped[datetime] = utc_timestamp()
    scheduler_latency_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    collection_recency_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    publication_latency_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    status: Mapped[VerificationStatus] = mapped_column(
        VERIFICATION_STATUS, nullable=False
    )
    failure_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    snapshot_checksum: Mapped[str] = sha256_hex()
    observed_at: Mapped[datetime] = created_timestamp()
