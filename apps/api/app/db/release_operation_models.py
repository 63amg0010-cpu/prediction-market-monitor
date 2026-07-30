"""Terminal release-operation receipt model."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    LargeBinary,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from .base import Base
from .columns import created_timestamp


def _receipt_sha(*, primary_key: bool = False) -> Mapped[str]:
    return mapped_column(String(64), primary_key=primary_key, nullable=False)


class ReleaseOperationReceipt(Base):
    """Terminal committed outcome for one exact reservation."""

    __tablename__: str = "release_operation_receipts"
    __table_args__: tuple[SchemaItem, ...] = (
        CheckConstraint(
            "predecessor_receipt_sha256 = reservation_receipt_sha256",
            name="release_operation_predecessor_reservation",
        ),
        CheckConstraint(
            "NOT retry_permitted OR (terminal_for_attempt AND NOT accepted)",
            name="release_operation_retry_terminal_failure",
        ),
        CheckConstraint(
            "NOT accepted OR (terminal_for_attempt AND NOT retry_permitted)",
            name="release_operation_accept_terminal",
        ),
        ForeignKeyConstraint(
            (
                "reservation_receipt_sha256",
                "reviewed_sha",
                "approved_plan_sha256",
                "activation_nonce",
                "dispatch_nonce",
                "attempt",
            ),
            (
                "release_operation_reservations.receipt_sha256",
                "release_operation_reservations.reviewed_sha",
                "release_operation_reservations.approved_plan_sha256",
                "release_operation_reservations.activation_nonce",
                "release_operation_reservations.dispatch_nonce",
                "release_operation_reservations.attempt",
            ),
            name="fk_release_operation_reservation_binding",
            ondelete="RESTRICT",
        ),
    )

    receipt_sha256: Mapped[str] = _receipt_sha(primary_key=True)
    canonical_receipt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    reservation_receipt_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey(
            "release_operation_reservations.receipt_sha256",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    predecessor_receipt_sha256: Mapped[str] = _receipt_sha()
    reviewed_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    activation_nonce: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dispatch_nonce: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    operation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    revision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt: Mapped[int] = mapped_column(nullable=False)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    terminal_for_attempt: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retry_permitted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state_before: Mapped[str] = mapped_column(String(32), nullable=False)
    state_after: Mapped[str] = mapped_column(String(32), nullable=False)
    enum_residue: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    committed_revision: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at_db: Mapped[datetime] = created_timestamp()
