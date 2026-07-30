"""Value-complete canonical report input manifests and record slices."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
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
from .enum_types import MANIFEST_CODEC, MANIFEST_ITEM_KIND, REPORT_ROLE
from .enums import ManifestCodec, ManifestItemKind, ReportRole
from .types import JsonValue


class ReportInputManifest(Base):
    """Deterministic gzip snapshot containing every formula-effective value."""

    __tablename__: str = "report_input_manifests"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("report_version_id", name="uq_manifest_report_version"),
        UniqueConstraint(
            "report_date_seoul",
            "input_set_hash",
            "schema_version",
            name="uq_manifest_input_identity",
        ),
        CheckConstraint(
            "uncompressed_byte_length > 0", name="manifest_payload_nonempty"
        ),
        CheckConstraint(
            sql_expression(
                (
                    "primary_window_end_utc > primary_window_start_utc",
                    "AND comparison_window_end_utc > comparison_window_start_utc",
                )
            ),
            name="manifest_windows_valid",
        ),
        CheckConstraint("retain_until > created_at", name="manifest_retention_window"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    report_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "daily_report_versions.id",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    report_date_seoul: Mapped[date] = mapped_column(nullable=False)
    report_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    primary_window_date_seoul: Mapped[date] = mapped_column(nullable=False)
    comparison_window_date_seoul: Mapped[date] = mapped_column(nullable=False)
    primary_window_start_utc: Mapped[datetime] = utc_timestamp()
    primary_window_end_utc: Mapped[datetime] = utc_timestamp()
    comparison_window_start_utc: Mapped[datetime] = utc_timestamp()
    comparison_window_end_utc: Mapped[datetime] = utc_timestamp()
    source_scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(80), nullable=False)
    formula_hash: Mapped[str] = sha256_hex()
    formula_constants: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    metric_version: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_hash: Mapped[str] = sha256_hex()
    category_version: Mapped[str] = mapped_column(String(80), nullable=False)
    category_hash: Mapped[str] = sha256_hex()
    governing_version_tuples: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    codec: Mapped[ManifestCodec] = mapped_column(MANIFEST_CODEC, nullable=False)
    compressed_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uncompressed_byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_payload_sha256: Mapped[str] = sha256_hex()
    input_set_hash: Mapped[str] = sha256_hex()
    created_at: Mapped[datetime] = created_timestamp()
    retain_until: Mapped[datetime] = utc_timestamp()


class ReportInputManifestItem(Base):
    """Canonical record or coverage value slice with restrictive live provenance."""

    __tablename__: str = "report_input_manifest_items"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "manifest_id", "item_kind", "role", "ordinal", name="uq_manifest_item"
        ),
        CheckConstraint("ordinal >= 0", name="manifest_item_ordinal_nonnegative"),
        CheckConstraint(
            sql_expression(
                (
                    "item_kind <> 'record' OR",
                    "num_nonnulls(live_post_version_id, post_version_tombstone_id) = 1",
                )
            ),
            name="record_item_has_exact_post_provenance",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    manifest_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("report_input_manifests.id", ondelete="RESTRICT"),
        nullable=False,
    )
    item_kind: Mapped[ManifestItemKind] = mapped_column(
        MANIFEST_ITEM_KIND, nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[ReportRole] = mapped_column(REPORT_ROLE, nullable=False)
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_entity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    source_entity_hash: Mapped[str | None] = sha256_hex(nullable=True)
    live_post_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("post_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    post_version_tombstone_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("report_input_tombstones.id", ondelete="RESTRICT"),
        nullable=True,
    )
    live_analysis_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("analyses.id", ondelete="RESTRICT"),
        nullable=True,
    )
    live_engagement_observation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("engagement_observations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    live_source_publication_manifest_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_run_publication_manifests.id", ondelete="RESTRICT"),
        nullable=True,
    )
    provenance_values: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    value_slice_sha256: Mapped[str] = sha256_hex()
