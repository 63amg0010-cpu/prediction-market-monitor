"""Source-local publication sequence and immutable run manifests."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from .base import Base
from .columns import created_timestamp, sha256_hex, sql_expression, uuid_primary_key


class SourcePublicationSequence(Base):
    """Monotonic source-local publication counter."""

    __tablename__: str = "source_publication_sequences"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("source_id", name="uq_publication_sequence_source"),
        CheckConstraint(
            "current_sequence >= 0", name="publication_sequence_nonnegative"
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = created_timestamp()


class SourceRunPublicationManifest(Base):
    """Successful run publication bound to its terminal page proof."""

    __tablename__: str = "source_run_publication_manifests"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("run_id", name="uq_publication_manifest_run"),
        UniqueConstraint(
            "source_id", "sequence", name="uq_publication_manifest_source_sequence"
        ),
        CheckConstraint("sequence > 0", name="publication_sequence_positive"),
        CheckConstraint(
            sql_expression(
                (
                    "(zero_post AND distinct_post_version_count = 0) OR",
                    "(NOT zero_post AND distinct_post_version_count > 0)",
                )
            ),
            name="zero_post_matches_count",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    terminal_page_commit_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("page_commits.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    final_chain_hash: Mapped[str] = sha256_hex()
    post_set_hash: Mapped[str] = sha256_hex()
    distinct_post_version_count: Mapped[int] = mapped_column(Integer, nullable=False)
    zero_post: Mapped[bool] = mapped_column(Boolean, nullable=False)
    committed_at: Mapped[datetime] = created_timestamp()
