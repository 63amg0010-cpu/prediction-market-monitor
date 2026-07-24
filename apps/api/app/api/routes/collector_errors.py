"""Secret-safe collector error translation at the HTTP boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from app.collection.base import CollectionError
from app.core.errors import correlation_id_from_header

if TYPE_CHECKING:
    from collections.abc import Awaitable


async def collection_call[T](
    operation: Awaitable[T], correlation_id: str | None = None
) -> T | JSONResponse:
    """Translate only typed collection failures into redacted responses."""
    try:
        return await operation
    except CollectionError as error:
        resolved = correlation_id_from_header(correlation_id)
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code.value,
                    "correlation_id": resolved,
                    "current_checkpoint_revision": error.current_checkpoint_revision,
                    "current_cursor": error.current_cursor,
                    "expected_page_ordinal": error.expected_page_ordinal,
                    "existing_commit_id": (
                        str(error.existing_commit_id)
                        if error.existing_commit_id is not None
                        else None
                    ),
                }
            },
            headers={"Cache-Control": "no-store", "X-Correlation-ID": resolved},
        )


__all__ = ("collection_call",)
