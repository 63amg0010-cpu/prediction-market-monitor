"""Typed response envelopes consumed by the workflow HTTP client."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003 - Pydantic resolves this at runtime.

from pydantic import BaseModel

from .base import CollectionErrorCode  # noqa: TC001 - Pydantic runtime field.


class OidcResponse(BaseModel):
    """GitHub Actions OIDC endpoint response."""

    value: str


class ExchangeResponse(BaseModel):
    """Service-token exchange response subset."""

    access_token: str


class CollectionErrorBody(BaseModel):
    code: CollectionErrorCode
    current_checkpoint_revision: int | None = None
    current_cursor: str | None = None
    expected_page_ordinal: int | None = None
    existing_commit_id: UUID | None = None


class CollectionErrorEnvelope(BaseModel):
    """Typed conflict envelope preserving recovery facts."""

    error: CollectionErrorBody


__all__ = (
    "CollectionErrorEnvelope",
    "ExchangeResponse",
    "OidcResponse",
)
