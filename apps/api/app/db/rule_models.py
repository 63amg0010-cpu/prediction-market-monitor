"""Immutable keyword rule sets and post-version matches."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from .base import Base
from .columns import created_timestamp, sha256_hex, uuid_primary_key


class KeywordRuleSet(Base):
    """Reviewed immutable keyword ruleset identity."""

    __tablename__: str = "keyword_rule_sets"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("version", name="uq_keyword_rule_set_version"),
        UniqueConstraint("rules_hash", name="uq_keyword_rule_set_hash"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    version: Mapped[str] = mapped_column(String(80), nullable=False)
    rules_hash: Mapped[str] = sha256_hex()
    reviewed_at: Mapped[datetime] = created_timestamp()


class KeywordRule(Base):
    """One normalized phrase and category in an immutable ruleset."""

    __tablename__: str = "keyword_rules"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "rule_set_id", "language", "normalized_phrase", name="uq_keyword_rule"
        ),
        CheckConstraint("language IN ('ko', 'en')", name="keyword_language_supported"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    rule_set_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("keyword_rule_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    language: Mapped[str] = mapped_column(String(2), nullable=False)
    phrase: Mapped[str] = mapped_column(String(300), nullable=False)
    normalized_phrase: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class PostMatch(Base):
    """Immutable rule result bound to an exact post version."""

    __tablename__: str = "post_matches"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("post_version_id", "rule_id", name="uq_post_version_rule"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    post_version_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("post_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rule_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("keyword_rules.id", ondelete="RESTRICT"),
        nullable=False,
    )
    matched: Mapped[bool] = mapped_column(Boolean, nullable=False)
    normalized_phrase: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    match_hash: Mapped[str] = sha256_hex()
    matched_at: Mapped[datetime] = created_timestamp()
