"""Reusable PostgreSQL column constructors."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import MappedColumn, mapped_column


def uuid_primary_key() -> MappedColumn[UUID]:
    """Create a database-generated UUID primary key."""
    return mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def uuid_reference(target: str, *, nullable: bool = False) -> MappedColumn[UUID]:
    """Create a UUID foreign-key column using an explicit target."""
    return mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(target, ondelete="RESTRICT"),
        nullable=nullable,
    )


def utc_timestamp(*, nullable: bool = False) -> MappedColumn[datetime]:
    """Create a timezone-aware timestamp column."""
    return mapped_column(DateTime(timezone=True), nullable=nullable)


def created_timestamp() -> MappedColumn[datetime]:
    """Create a database-clock insertion timestamp."""
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def sha256_hex(*, nullable: bool = False) -> MappedColumn[str]:
    """Create a lowercase SHA-256 hexadecimal storage column."""
    return mapped_column(String(64), nullable=nullable)


def nonnegative_integer(*, default: int | None = None) -> MappedColumn[int]:
    """Create a non-null integer with an optional server default."""
    server_default = None if default is None else text(str(default))
    return mapped_column(Integer, nullable=False, server_default=server_default)


def seoul_date() -> MappedColumn[date]:
    """Create a non-null Seoul calendar date column."""
    return mapped_column(Date, nullable=False)


def sql_expression(parts: tuple[str, ...]) -> str:
    """Join typed SQL fragments with one separating space."""
    return " ".join(parts)
