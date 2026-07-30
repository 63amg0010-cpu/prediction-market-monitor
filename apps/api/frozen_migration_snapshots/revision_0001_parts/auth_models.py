"""Authorization, source registry, principal, and session models."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
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

from .base import Base
from .columns import (
    created_timestamp,
    sha256_hex,
    sql_expression,
    utc_timestamp,
    uuid_primary_key,
)
from .enum_types import (
    AUTHORIZATION_STATUS,
    COUNTRY,
    NONCE_PURPOSE,
    PRINCIPAL_KIND,
    SOURCE_PLATFORM,
)
from .enums import (
    AuthorizationStatus,
    Country,
    NoncePurpose,
    PrincipalKind,
    SourcePlatform,
)
from .types import JsonValue


class CommunitySource(Base):
    """Reviewed source registry with fail-closed enablement."""

    __tablename__: str = "community_sources"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "platform", "external_key", "scope_version", name="uq_source_scope"
        ),
        CheckConstraint(
            "NOT enabled OR active_authorization_id IS NOT NULL",
            name="enabled_requires_authorization",
        ),
        Index(
            "uq_community_sources_one_kr_finance_alternative",
            text("(1)"),
            unique=True,
            postgresql_where=text(
                "enabled AND platform IN ('toss_securities', 'naver_finance')"
            ),
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    country: Mapped[Country] = mapped_column(COUNTRY, nullable=False)
    platform: Mapped[SourcePlatform] = mapped_column(SOURCE_PLATFORM, nullable=False)
    external_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    scope_version: Mapped[str] = mapped_column(String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    active_authorization_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "source_authorization_decisions.id",
            name="fk_sources_active_authorization",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = created_timestamp()


class SourceAuthorizationDecision(Base):
    """Append-only evidence for one exact source access scope."""

    __tablename__: str = "source_authorization_decisions"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint(
            "source_id", "evidence_sha256", "effective_at", name="uq_auth_evidence"
        ),
        CheckConstraint(
            sql_expression(
                (
                    "status <> 'approved' OR",
                    "(expires_at IS NOT NULL AND expires_at > effective_at",
                    "AND revoked_at IS NULL)",
                )
            ),
            name="approved_window_valid",
        ),
        CheckConstraint(
            "status <> 'revoked' OR revoked_at IS NOT NULL",
            name="revoked_has_timestamp",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("community_sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[AuthorizationStatus] = mapped_column(
        AUTHORIZATION_STATUS, nullable=False
    )
    evidence_sha256: Mapped[str] = sha256_hex()
    evidence_location: Mapped[str] = mapped_column(Text, nullable=False)
    issuer: Mapped[str] = mapped_column(String(200), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(200), nullable=False)
    permitted_scope: Mapped[JsonValue] = mapped_column(JSONB, nullable=False)
    effective_at: Mapped[datetime] = utc_timestamp()
    expires_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    revoked_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    decided_at: Mapped[datetime] = created_timestamp()


class ServicePrincipal(Base):
    """Revocable server-side identity for one external actor."""

    __tablename__: str = "service_principals"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("kind", "subject", name="uq_principal_kind_subject"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    kind: Mapped[PrincipalKind] = mapped_column(PRINCIPAL_KIND, nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    revoked_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    created_at: Mapped[datetime] = created_timestamp()


class PrincipalCredentialVersion(Base):
    """Rotatable credential verifier bound to a principal version."""

    __tablename__: str = "principal_credential_versions"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("principal_id", "version", name="uq_principal_version"),
        CheckConstraint("version > 0", name="positive_version"),
        CheckConstraint(
            "octet_length(verifier_hash) = 32", name="verifier_hash_sha256"
        ),
        CheckConstraint("valid_until > valid_from", name="credential_window_valid"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("service_principals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    verifier_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    valid_from: Mapped[datetime] = utc_timestamp()
    valid_until: Mapped[datetime] = utc_timestamp()
    revoked_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    created_at: Mapped[datetime] = created_timestamp()


class AdminSession(Base):
    """Revocable single-admin session and CSRF token state."""

    __tablename__: str = "admin_sessions"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("session_token_hash", name="uq_admin_session_token"),
        CheckConstraint("expires_at > created_at", name="session_window_valid"),
        CheckConstraint(
            "octet_length(session_token_hash) = 32",
            name="session_token_hash_sha256",
        ),
    )

    id: Mapped[UUID] = uuid_primary_key()
    principal_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("service_principals.id", ondelete="RESTRICT"),
        nullable=False,
    )
    session_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    csrf_current_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    csrf_prior_hash: Mapped[bytes | None] = mapped_column(
        LargeBinary(32), nullable=True
    )
    expires_at: Mapped[datetime] = utc_timestamp()
    rotated_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    revoked_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    created_at: Mapped[datetime] = created_timestamp()


class LoginRateLimit(Base):
    """Database-clock login failure bucket keyed by a one-way client hash."""

    __tablename__: str = "login_rate_limits"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("client_hash", "bucket_start", name="uq_login_bucket"),
        CheckConstraint("failure_count >= 0", name="failure_count_nonnegative"),
        CheckConstraint("bucket_end > bucket_start", name="login_bucket_window_valid"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    client_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    bucket_start: Mapped[datetime] = utc_timestamp()
    bucket_end: Mapped[datetime] = utc_timestamp()
    failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = utc_timestamp(nullable=True)
    updated_at: Mapped[datetime] = created_timestamp()


class OneUseNonce(Base):
    """Expiring replay barrier separated by exchange purpose."""

    __tablename__: str = "one_use_nonces"
    __table_args__: tuple[SchemaItem, ...] = (
        UniqueConstraint("purpose", "nonce_hash", name="uq_nonce_purpose_hash"),
        CheckConstraint("expires_at > created_at", name="nonce_window_valid"),
        CheckConstraint("octet_length(nonce_hash) = 32", name="nonce_hash_sha256"),
    )

    id: Mapped[UUID] = uuid_primary_key()
    principal_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("service_principals.id", ondelete="RESTRICT"),
        nullable=True,
    )
    purpose: Mapped[NoncePurpose] = mapped_column(NONCE_PURPOSE, nullable=False)
    nonce_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    external_identity: Mapped[str | None] = mapped_column(String(300), nullable=True)
    expires_at: Mapped[datetime] = utc_timestamp()
    used_at: Mapped[datetime | None] = utc_timestamp(nullable=True)
    created_at: Mapped[datetime] = created_timestamp()
