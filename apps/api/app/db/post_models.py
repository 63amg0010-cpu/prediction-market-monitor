"""Author-free posts, immutable revisions, and engagement observations."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, column_property, mapped_column
from sqlalchemy.sql import literal_column
from sqlalchemy.sql.schema import SchemaItem

from app.domain.enums import PostVersionReason

from .base import Base
from .columns import created_timestamp, sha256_hex, utc_timestamp, uuid_primary_key
from .enum_types import POST_VERSION_REASON


class Post(Base):
    """Author-free stable source post identity and current revision pointer."""

    __tablename__: str = "posts"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("source_id", "source_post_id", name="uq_post_source_identity"),
        CheckConstraint("language IN ('ko', 'en')", name="post_language_supported"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_post_id: Mapped[str] = mapped_column(String(300), nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = utc_timestamp()
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "post_versions.id",
            name="fk_posts_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = created_timestamp()
    updated_at: Mapped[datetime] = created_timestamp()


class PostVersion(Base):
    """Immutable full accepted title and body revision."""

    __tablename__: str = "post_versions"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("post_id", "revision", name="uq_post_version_revision"),
        UniqueConstraint("post_id", "content_hash", name="uq_post_version_content"),
        CheckConstraint("revision > 0", name="post_revision_positive"),
        CheckConstraint("body_bytes BETWEEN 0 AND 262144", name="post_body_size_limit"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    post_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("posts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = sha256_hex()
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = column_property(
        literal_column("search_text", Text(collation="C"))
    )
    body_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[PostVersionReason] = mapped_column(
        POST_VERSION_REASON, nullable=False
    )
    collected_at: Mapped[datetime] = created_timestamp()


class EngagementObservation(Base):
    """Immutable nullable engagement values observed during one source run."""

    __tablename__: str = "engagement_observations"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "post_version_id", "source_run_id", name="uq_engagement_version_run"
        ),
        CheckConstraint(
            "comments_count IS NULL OR comments_count >= 0",
            name="comments_count_nonnegative",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    post_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("post_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    observed_at: Mapped[datetime] = utc_timestamp()
    comments_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upvote_or_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    engagement_hash: Mapped[str] = sha256_hex()
