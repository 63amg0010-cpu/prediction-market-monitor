"""Static skip-observation table owned by revision 20260722_0004."""

from datetime import datetime
from typing import Final, cast
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv
from sqlalchemy.sql.schema import SchemaItem

from . import revision_0001
from .revision_0001_parts.base import Base
from .revision_0001_parts.columns import (
    created_timestamp,
    sha256_hex,
    sql_expression,
    uuid_primary_key,
)
from .revision_0001_parts.enum_types import SOURCE_PLATFORM
from .revision_0001_parts.enums import SourcePlatform


class CollectionSkipObservation(Base):
    """Idempotent redacted provider observation and its server decision proof."""

    __tablename__: str = "collection_skip_observations"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("run_id", "idempotency_key", name="uq_skip_run_key"),
        CheckConstraint("attempt BETWEEN 1 AND 3", name="skip_attempt_range"),
        CheckConstraint(
            sql_expression(
                (
                    "(http_status IN (401, 403)",
                    "AND failure_code = 'provider_authorization_rejected'",
                    "AND decision_kind = 'policy') OR",
                    "(http_status = 429",
                    "AND failure_code = 'provider_quota_exhausted'",
                    "AND decision_kind = 'quota')",
                )
            ),
            name=cast("str", conv("skip_observation_status_code_pair")),
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    command_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("collection_commands.id", ondelete="RESTRICT"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = sha256_hex()
    actor_principal_id: Mapped[str] = mapped_column(String(300), nullable=False)
    provider: Mapped[SourcePlatform] = mapped_column(SOURCE_PLATFORM, nullable=False)
    route: Mapped[str] = mapped_column(String(300), nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_code: Mapped[str] = mapped_column(String(120), nullable=False)
    decision_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    decision_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    evidence_sha256: Mapped[str] = sha256_hex()
    evidence_location: Mapped[str] = mapped_column(Text, nullable=False)
    stored_response: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = created_timestamp()


TABLE: Final[Table] = cast("Table", CollectionSkipObservation.__table__)
PARENT_METADATA = revision_0001.metadata
