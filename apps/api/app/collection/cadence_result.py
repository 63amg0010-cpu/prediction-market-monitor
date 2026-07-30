"""Schema-closed operation result consumed only by the cadence recorder."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import ClassVar, Literal

import httpx2
from pydantic import UUID4, BaseModel, ConfigDict, Field, model_validator

from app.services.configuration.canonical import canonical_sha256


class CadenceSourceResult(BaseModel):
    """One source-local result hash with no provider content."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")
    source_id: UUID4
    status: Literal["succeeded", "failed"]
    code: Literal[
        "ok",
        "transient_timeout",
        "transient_transport",
        "operation_rejected",
        "unexpected_failure",
    ]
    retry_classification: Literal[
        "not_applicable", "safe_terminal", "hold"
    ]
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def require_closed_outcome_shape(self) -> CadenceSourceResult:
        """Bind success and retry classification to reviewed typed codes."""
        success = (
            self.status == "succeeded"
            and self.code == "ok"
            and self.retry_classification == "not_applicable"
        )
        safe = (
            self.status == "failed"
            and self.code in {"transient_timeout", "transient_transport"}
            and self.retry_classification == "safe_terminal"
        )
        held = (
            self.status == "failed"
            and self.code in {"operation_rejected", "unexpected_failure"}
            and self.retry_classification == "hold"
        )
        hash_bound = self.status == "succeeded" or self.receipt_sha256 == (
            failure_receipt_hash(self.source_id, self.code)
        )
        if not ((success or safe or held) and hash_bound):
            message = "cadence_source_result_shape_invalid"
            raise ValueError(message)
        return self


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


@dataclass(frozen=True, slots=True)
class CadenceFailureContext:
    """Exact public identity needed to report one observable failure."""

    schedule_kind: Literal["collection", "verifier"]
    slot_key: str
    source_ids: tuple[UUID4, ...]
    started_at: str
    completed_at: str


def result_hash(value: BaseModel) -> str:
    """Hash one already schema-closed source result."""
    return canonical_sha256(value)


def write_result(path: str, result: CadenceOperationResult) -> None:
    """Write owner-local public-safe JSON without logging its path or bytes."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        result.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    _ = target.write_bytes(encoded)


def opaque_hash(*parts: str) -> str:
    """Hash bounded result identity where no model exists."""
    return sha256("\x00".join(parts).encode()).hexdigest()


def failure_receipt_hash(source_id: UUID4, code: str) -> str:
    """Bind a public failure code to its source without exception content."""
    return opaque_hash("cadence-operation-source.v1", str(source_id), code)


def write_failure_result(
    path: str,
    context: CadenceFailureContext,
    error: Exception,
) -> None:
    """Write only a reviewed failure class, never exception identity/content."""
    if isinstance(error, (TimeoutError, httpx2.TimeoutException)):
        code = "transient_timeout"
        retry = "safe_terminal"
    elif isinstance(error, (ConnectionError, httpx2.TransportError)):
        code = "transient_transport"
        retry = "safe_terminal"
    else:
        code = "unexpected_failure"
        retry = "hold"
    write_result(
        path,
        CadenceOperationResult(
            schedule_kind=context.schedule_kind,
            slot_key=context.slot_key,
            started_at=context.started_at,
            completed_at=context.completed_at,
            source_results=tuple(
                CadenceSourceResult(
                    source_id=source_id,
                    status="failed",
                    code=code,
                    retry_classification=retry,
                    receipt_sha256=failure_receipt_hash(source_id, code),
                )
                for source_id in context.source_ids
            ),
        ),
    )


__all__ = (
    "CadenceFailureContext",
    "CadenceOperationResult",
    "CadenceSourceResult",
    "failure_receipt_hash",
    "opaque_hash",
    "result_hash",
    "write_failure_result",
    "write_result",
)
