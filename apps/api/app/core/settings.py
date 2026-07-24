"""Fail-closed identity configuration parsed from environment variables."""

from __future__ import annotations

from typing import Annotated, ClassVar, TypedDict

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    StringConstraints,
    TypeAdapter,
    field_validator,
)
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, SettingsConfigDict

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_STRING_TUPLE_ADAPTER = TypeAdapter(tuple[NonBlank, ...])
MINIMUM_SECRET_BYTES = 32
_LENGTH_ERROR_CODE = "secret_length"
_LENGTH_ERROR_MESSAGE = "security secret must contain at least 256 bits"
_ARGON2_ERROR_CODE = "argon2id_required"
_ARGON2_ERROR_MESSAGE = "admin password hash must use Argon2id"


class RedactedIdentityMetadata(TypedDict):
    """Non-secret identity configuration safe for diagnostics."""

    audience: str
    issuer: str
    key_id: str
    bff_credential_version: str
    worker_credential_version: str


class IdentitySettings(BaseSettings):
    """Required security environment with no development defaults."""

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="forbid",
        frozen=True,
    )

    api_base_url: AnyHttpUrl
    service_token_key_id: NonBlank
    service_token_issuer_private_key: SecretStr
    service_token_issuer_public_key: SecretStr
    bff_client_credential: SecretStr
    bff_credential_version: NonBlank
    worker_bootstrap_secret: SecretStr
    worker_credential_version: NonBlank
    cron_secret: SecretStr
    admin_password_argon2id_hash: SecretStr
    session_hmac_secret: SecretStr
    github_repository: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    ]
    github_workflow_refs: tuple[NonBlank, ...] = Field(min_length=1)
    github_allowed_refs: tuple[NonBlank, ...] = Field(min_length=1)
    github_allowed_environments: tuple[NonBlank, ...] = Field(min_length=1)

    @field_validator(
        "github_workflow_refs",
        "github_allowed_refs",
        "github_allowed_environments",
        mode="before",
    )
    @classmethod
    def parse_json_tuple(cls, value: str | tuple[str, ...]) -> tuple[str, ...]:
        """Parse tuple settings exclusively from explicit JSON arrays."""
        if isinstance(value, str):
            return _STRING_TUPLE_ADAPTER.validate_json(value)
        return value

    @field_validator(
        "service_token_issuer_private_key",
        "service_token_issuer_public_key",
        "bff_client_credential",
        "worker_bootstrap_secret",
        "cron_secret",
        "session_hmac_secret",
    )
    @classmethod
    def require_secret_material(cls, value: SecretStr) -> SecretStr:
        """Reject secret material shorter than 256 bits."""
        if len(value.get_secret_value().encode()) < MINIMUM_SECRET_BYTES:
            raise PydanticCustomError(_LENGTH_ERROR_CODE, _LENGTH_ERROR_MESSAGE)
        return value

    @field_validator("admin_password_argon2id_hash")
    @classmethod
    def require_argon2id(cls, value: SecretStr) -> SecretStr:
        """Reject administrator hashes that are not Argon2id."""
        if not value.get_secret_value().startswith("$argon2id$"):
            raise PydanticCustomError(_ARGON2_ERROR_CODE, _ARGON2_ERROR_MESSAGE)
        return value

    def redacted_metadata(self) -> RedactedIdentityMetadata:
        """Return only non-secret configuration metadata."""
        return RedactedIdentityMetadata(
            audience=str(self.api_base_url).rstrip("/"),
            issuer="monitor-api",
            key_id=self.service_token_key_id,
            bff_credential_version=self.bff_credential_version,
            worker_credential_version=self.worker_credential_version,
        )
