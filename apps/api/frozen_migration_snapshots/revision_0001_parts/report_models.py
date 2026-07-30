"""Append-only daily report pointers and scalar-rich versions."""

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
from .enum_types import REPORT_STATUS
from .enums import ReportStatus
from .types import JsonValue


class DailyReport(Base):
    """Locked date pointer selecting the latest immutable report version."""

    __tablename__: str = "daily_reports"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("report_date_seoul", name="uq_daily_report_date"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    report_date_seoul: Mapped[date] = mapped_column(nullable=False)
    latest_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "daily_report_versions.id",
            name="fk_daily_reports_latest_version",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=False,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = created_timestamp()


class DailyReportVersion(Base):
    """Immutable report projection with every displayed scalar and payload hash."""

    __tablename__: str = "daily_report_versions"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "report_date_seoul", "revision", name="uq_report_date_revision"
        ),
        UniqueConstraint(
            "report_date_seoul",
            "input_set_hash",
            "report_schema_version",
            name="uq_report_input_identity",
        ),
        UniqueConstraint("manifest_id", name="uq_report_version_manifest"),
        CheckConstraint("revision > 0", name="report_revision_positive"),
        CheckConstraint(
            sql_expression(
                (
                    "candidate_count >= 0 AND valid_analysis_count >= 0",
                    "AND pending_count >= 0 AND relevant_count >= 0",
                )
            ),
            name="report_counts_nonnegative",
        ),
        CheckConstraint(
            "pending_count = candidate_count - valid_analysis_count",
            name="report_pending_count_exact",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "analysis_coverage_numerator = valid_analysis_count",
                    "AND analysis_coverage_denominator = candidate_count",
                )
            ),
            name="report_coverage_counts_exact",
        ),
        CheckConstraint(
            "report_payload_byte_length = octet_length(report_payload)",
            name="report_payload_length_exact",
        ),
        CheckConstraint("retain_until > created_at", name="report_retention_window"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    report_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("daily_reports.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report_date_seoul: Mapped[date] = mapped_column(nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("daily_report_versions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    input_set_hash: Mapped[str] = sha256_hex()
    report_schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "report_input_manifests.id",
            name="fk_report_versions_manifest",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        nullable=False,
    )
    primary_window_start_utc: Mapped[datetime] = utc_timestamp()
    primary_window_end_utc: Mapped[datetime] = utc_timestamp()
    comparison_window_start_utc: Mapped[datetime] = utc_timestamp()
    comparison_window_end_utc: Mapped[datetime] = utc_timestamp()
    formula_version: Mapped[str] = mapped_column(String(80), nullable=False)
    formula_hash: Mapped[str] = sha256_hex()
    metric_version: Mapped[str] = mapped_column(String(80), nullable=False)
    metric_hash: Mapped[str] = sha256_hex()
    category_version: Mapped[str] = mapped_column(String(80), nullable=False)
    category_hash: Mapped[str] = sha256_hex()
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_analysis_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False)
    relevant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    positive_count: Mapped[int] = mapped_column(Integer, nullable=False)
    neutral_count: Mapped[int] = mapped_column(Integer, nullable=False)
    negative_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unknown_sentiment_count: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_coverage_numerator: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_coverage_denominator: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_coverage_decimal: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    comments_sum: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments_known_count: Mapped[int] = mapped_column(Integer, nullable=False)
    comments_unknown_count: Mapped[int] = mapped_column(Integer, nullable=False)
    score_sum: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_known_count: Mapped[int] = mapped_column(Integer, nullable=False)
    score_unknown_count: Mapped[int] = mapped_column(Integer, nullable=False)
    highlights: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    rising_keywords: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    source_coverage: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    status: Mapped[ReportStatus] = mapped_column(REPORT_STATUS, nullable=False)
    report_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    report_payload_byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    report_payload_sha256: Mapped[str] = sha256_hex()
    created_at: Mapped[datetime] = created_timestamp()
    retain_until: Mapped[datetime] = utc_timestamp()
