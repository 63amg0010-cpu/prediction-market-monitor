"""Generic append-only RFC 8785 release receipt registry."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from app.domain.types import JsonValue

from .base import Base
from .columns import created_timestamp


class ReleaseReceiptChain(Base):
    """Common identity metadata for every immutable release chain node."""

    __tablename__: str = "release_receipt_chain"
    __table_args__: tuple[SchemaItem, ...] = (
        CheckConstraint(
            "receipt_sha256 ~ '^[0-9a-f]{64}$'",
            name="release_receipt_chain_sha",
        ),
        CheckConstraint(
            "attempt >= 0",
            name="release_receipt_chain_attempt",
        ),
        CheckConstraint(
            "NOT accepted OR NOT retry_permitted",
            name="release_receipt_chain_accept_retry",
        ),
        CheckConstraint(
            "NOT retry_permitted OR terminal_for_attempt",
            name="release_receipt_chain_retry_terminal",
        ),
    )

    receipt_sha256: Mapped[str] = mapped_column(
        String(64), primary_key=True, nullable=False
    )
    canonical_receipt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    command: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewed_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_round_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_launch_sha256s: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    activation_nonce: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dispatch_nonce: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    attempt: Mapped[int] = mapped_column(nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    terminal_for_attempt: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retry_permitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    predecessor_receipt_sha256: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("release_receipt_chain.receipt_sha256", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at_db: Mapped[datetime] = created_timestamp()


__all__ = ("ReleaseReceiptChain",)
