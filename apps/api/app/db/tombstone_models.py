"""Deletion provenance and manifest linkage for retained report inputs."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from app.domain.enums import TombstoneDeletionReason, TombstoneEntityKind

from .base import Base
from .columns import created_timestamp, sha256_hex, utc_timestamp, uuid_primary_key
from .enum_types import TOMBSTONE_DELETION_REASON, TOMBSTONE_ENTITY_KIND


class ReportInputTombstone(Base):
    """Body-free immutable provenance created before source-row deletion."""

    __tablename__: str = "report_input_tombstones"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "entity_kind",
            "source_entity_id",
            "source_entity_hash",
            "manifest_value_slice_sha256",
            name="uq_report_input_tombstone_identity",
        ),
        CheckConstraint("retain_until > deleted_at", name="tombstone_retention_window"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    entity_kind: Mapped[TombstoneEntityKind] = mapped_column(
        TOMBSTONE_ENTITY_KIND, nullable=False
    )
    source_entity_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_entity_hash: Mapped[str] = sha256_hex()
    source_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=True,
    )
    published_or_observed_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    deleted_at: Mapped[datetime] = created_timestamp()
    deletion_reason: Mapped[TombstoneDeletionReason] = mapped_column(
        TOMBSTONE_DELETION_REASON, nullable=False
    )
    manifest_value_slice_sha256: Mapped[str] = sha256_hex()
    first_manifest_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    retain_until: Mapped[datetime] = utc_timestamp()


class ReportInputManifestItemTombstone(Base):
    """Many-to-many tombstone switch for one retained manifest value slice."""

    __tablename__: str = "report_input_manifest_item_tombstones"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "manifest_item_id", "tombstone_id", name="uq_manifest_item_tombstone"
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    manifest_item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("report_input_manifest_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tombstone_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("report_input_tombstones.id", ondelete="RESTRICT"),
        nullable=False,
    )


class ReportInputManifestItemMatch(Base):
    """Restrictive link from one value slice to every live rule match."""

    __tablename__: str = "report_input_manifest_item_matches"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "manifest_item_id", "post_match_id", name="uq_manifest_item_match"
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    manifest_item_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("report_input_manifest_items.id", ondelete="RESTRICT"),
        nullable=False,
    )
    post_match_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("post_matches.id", ondelete="RESTRICT"),
        nullable=False,
    )
