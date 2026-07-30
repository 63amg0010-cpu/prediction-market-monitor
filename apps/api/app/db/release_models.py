"""Durable release-operation identity and receipt ledger models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.schema import SchemaItem

from app.domain.types import JsonValue

from .base import Base
from .columns import created_timestamp, utc_timestamp
from .release_operation_models import ReleaseOperationReceipt

__all__ = [
    "ReleaseNoSpendReceipt",
    "ReleaseOperationReceipt",
    "ReleaseOperationReservation",
    "ReleaseRoot",
]


def _receipt_sha(*, primary_key: bool = False) -> Mapped[str]:
    return mapped_column(String(64), primary_key=primary_key, nullable=False)


class ReleaseRoot(Base):
    """Immutable reviewed release root."""

    __tablename__: str = "release_roots"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "reviewed_sha",
            "approved_plan_sha256",
            "approval_round_id",
            name="uq_release_root_review_round",
        ),
        UniqueConstraint("activation_nonce", name="uq_release_root_activation"),
        UniqueConstraint(
            "receipt_sha256",
            "reviewed_sha",
            "approved_plan_sha256",
            "activation_nonce",
            name="uq_release_root_binding",
        ),
        CheckConstraint(
            "receipt_sha256 ~ '^[0-9a-f]{64}$'", name="release_root_receipt_sha"
        ),
    )

    receipt_sha256: Mapped[str] = _receipt_sha(primary_key=True)
    canonical_receipt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    reviewed_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_round_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_launch_sha256s: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    activation_nonce: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at_db: Mapped[datetime] = created_timestamp()


class ReleaseNoSpendReceipt(Base):
    """Accepted no-spend receipt bound to one root."""

    __tablename__: str = "release_no_spend_receipts"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "root_receipt_sha256",
            "activation_nonce",
            name="uq_release_no_spend_root_activation",
        ),
        CheckConstraint("accepted", name="release_no_spend_accepted"),
        CheckConstraint(
            "predecessor_receipt_sha256 = root_receipt_sha256",
            name="release_no_spend_predecessor_root",
        ),
        ForeignKeyConstraint(
            (
                "root_receipt_sha256",
                "reviewed_sha",
                "approved_plan_sha256",
                "activation_nonce",
            ),
            (
                "release_roots.receipt_sha256",
                "release_roots.reviewed_sha",
                "release_roots.approved_plan_sha256",
                "release_roots.activation_nonce",
            ),
            name="fk_release_no_spend_root_binding",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "receipt_sha256",
            "reviewed_sha",
            "approved_plan_sha256",
            "activation_nonce",
            name="uq_release_no_spend_binding",
        ),
    )

    receipt_sha256: Mapped[str] = _receipt_sha(primary_key=True)
    canonical_receipt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    root_receipt_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("release_roots.receipt_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    activation_nonce: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    predecessor_receipt_sha256: Mapped[str] = _receipt_sha()
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at_db: Mapped[datetime] = created_timestamp()


class ReleaseOperationReservation(Base):
    """Unique dispatch reservation optionally claimed by one workflow run."""

    __tablename__: str = "release_operation_reservations"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "activation_nonce",
            "dispatch_nonce",
            name="uq_release_reservation_dispatch",
        ),
        CheckConstraint(
            "(claimed_run_id IS NULL) = (claimed_at_db IS NULL)",
            name="release_reservation_claim_pair",
        ),
        CheckConstraint(
            "attempt >= 1",
            name="release_reservation_attempt_positive",
        ),
        UniqueConstraint(
            "receipt_sha256",
            "reviewed_sha",
            "approved_plan_sha256",
            "activation_nonce",
            "dispatch_nonce",
            "attempt",
            name="uq_release_reservation_binding",
        ),
        Index(
            "uq_release_reservation_claimed_run",
            "repository",
            "claimed_run_id",
            unique=True,
            postgresql_where=text("claimed_run_id IS NOT NULL"),
        ),
    )

    receipt_sha256: Mapped[str] = _receipt_sha(primary_key=True)
    canonical_receipt: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    predecessor_receipt_sha256: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("release_receipt_chain.receipt_sha256", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    approved_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_round_id: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_launch_sha256s: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    activation_nonce: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dispatch_nonce: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    repository: Mapped[str] = mapped_column(String(200), nullable=False)
    git_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    workflow_file: Mapped[str] = mapped_column(String(200), nullable=False)
    event_name: Mapped[str] = mapped_column(String(32), nullable=False)
    display_title: Mapped[str] = mapped_column(Text, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    operation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    revision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt: Mapped[int] = mapped_column(nullable=False)
    reserved_at_db: Mapped[datetime] = created_timestamp()
    selection_floor_at: Mapped[datetime] = utc_timestamp()
    claimed_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    claimed_run_attempt: Mapped[int | None] = mapped_column(nullable=True)
    claimed_at_db: Mapped[datetime | None] = utc_timestamp(nullable=True)
