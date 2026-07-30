"""Schema-closed operation result consumed only by the cadence recorder."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field

from app.services.configuration.canonical import canonical_sha256


class CadenceSourceResult(BaseModel):
    """One source-local result hash with no provider content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    source_id: UUID4
    succeeded: bool
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CadenceOperationResult(BaseModel):
    """Exact two-source legacy-operation outcome."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True
    )
    schema_version: Literal["cadence-operation-result.v1"] = Field(
        default="cadence-operation-result.v1", alias="schema"
    )
    schedule_kind: Literal["collection", "verifier"]
    slot_key: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00Z$")
    started_at: str
    completed_at: str
    source_results: tuple[CadenceSourceResult, ...] = Field(
        min_length=2, max_length=2
    )


def result_hash(value: BaseModel) -> str:
    """Hash one already schema-closed source result."""
    return canonical_sha256(value)


def write_result(path: str, result: CadenceOperationResult) -> None:
    """Write owner-local public-safe JSON without logging its path or bytes."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    _ = target.write_bytes(encoded)


def opaque_hash(*parts: str) -> str:
    """Hash bounded result identity where no model exists."""
    return sha256("\x00".join(parts).encode()).hexdigest()


__all__ = (
    "CadenceOperationResult",
    "CadenceSourceResult",
    "opaque_hash",
    "result_hash",
    "write_result",
)
