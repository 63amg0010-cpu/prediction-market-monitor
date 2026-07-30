"""Independent verifier cursors and immutable expected-slot observations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
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
from .enum_types import VERIFICATION_STATUS
from .enums import VerificationStatus


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
    snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
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
