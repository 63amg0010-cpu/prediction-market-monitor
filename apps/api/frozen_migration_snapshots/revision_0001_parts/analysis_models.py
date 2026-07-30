"""Version-bound analysis queue and immutable analysis outputs."""

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
    text,
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
from .enum_types import ANALYSIS_STATE, QUEUE_STATUS, SENTIMENT
from .enums import AnalysisState, QueueStatus, Sentiment


class AnalysisQueueItem(Base):
    """Leaseable work item pinned to one post version and schema tuple."""

    __tablename__: str = "analysis_queue"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "post_id", "post_version_id", name="uq_analysis_queue_post_version"
        ),
        CheckConstraint("attempts BETWEEN 0 AND 3", name="analysis_attempt_range"),
        CheckConstraint(
            sql_expression(
                (
                    "status <> 'leased' OR",
                    "(lease_token_hash IS NOT NULL AND lease_expires_at IS NOT NULL)",
                )
            ),
            name="leased_analysis_has_lease",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    post_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    post_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("post_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_hash: Mapped[str] = sha256_hex()
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[QueueStatus] = mapped_column(
        QUEUE_STATUS, nullable=False, server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    available_at: Mapped[datetime] = utc_timestamp()
    lease_token_hash: Mapped[bytes | None] = mapped_column(
        LargeBinary(32), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    leased_by_principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("service_principals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = created_timestamp()
    updated_at: Mapped[datetime] = created_timestamp()


class Analysis(Base):
    """Immutable parsed analysis state for an exact version tuple."""

    __tablename__: str = "analyses"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "post_version_id",
            "prompt_version",
            "model_version",
            "schema_version",
            name="uq_analysis_version_tuple",
        ),
        CheckConstraint(
            sql_expression(
                (
                    "(state = 'valid' AND relevance IS NOT NULL) OR",
                    "(state <> 'valid' AND relevance IS NULL AND sentiment IS NULL)",
                )
            ),
            name="analysis_values_match_state",
        ),
        CheckConstraint(
            "relevance IS TRUE OR sentiment IS NULL",
            name="sentiment_only_for_relevant",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    post_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("post_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[AnalysisState] = mapped_column(ANALYSIS_STATE, nullable=False)
    relevance: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sentiment: Mapped[Sentiment | None] = mapped_column(SENTIMENT, nullable=True)
    topics: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    output_hash: Mapped[str] = sha256_hex()
    analyzed_at: Mapped[datetime] = created_timestamp()
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
