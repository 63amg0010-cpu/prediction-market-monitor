"""Static verification snapshot tables owned by revision 20260722_0003."""

from datetime import datetime
from typing import Final, cast
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Table,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from . import revision_0001
from .revision_0001_parts.base import Base
from .revision_0001_parts.columns import (
    sha256_hex,
    sql_expression,
    utc_timestamp,
    uuid_primary_key,
)
from .revision_0001_parts.enum_types import COUNTRY
from .revision_0001_parts.enums import Country


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


TABLES: Final[tuple[Table, ...]] = (
    cast("Table", VerificationSnapshotRecord.__table__),
    cast("Table", VerificationSnapshotSource.__table__),
    cast("Table", VerificationSnapshotUse.__table__),
)

PARENT_METADATA = revision_0001.metadata
