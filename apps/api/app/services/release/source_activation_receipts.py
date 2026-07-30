"""Schema-closed canonical receipts for source activation."""

# pyright: reportUnnecessaryComparison=false
# ruff: noqa: D101, D102, TC001, TC003

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Annotated, ClassVar, Literal, assert_never
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from .source_activation_domain import ActivationTransition

Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
MAX_RECEIPT_BYTES = 65_536
Phase = Literal["reserve", "commit", "reprepare", "restore"]


class ClosedModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ActivateRequest(ClosedModel):
    phase: Phase
    database_url_env: str
    activation_nonce: UUID
    expected_sha: Sha
    json_out: Path
    attestation: Path | None = None
    free_tier_result: Path | None = None
    binding_handshake_receipt: Path | None = None
    activation_reserve_receipt: Path | None = None
    binding_finalize_receipt: Path | None = None
    attestation_generation: int | None = None
    failed_reservation_receipt: Path | None = None
    previous_attestation_receipt: Path | None = None
    activation_evidence_receipt: Path | None = None
    binding_restore_receipt: Path | None = None
    restore_verification_receipt: Path | None = None

    @model_validator(mode="after")
    def require_phase_arguments(self) -> ActivateRequest:
        shared = (self.attestation, self.free_tier_result)
        match self.phase:
            case "reserve":
                required = (*shared, self.binding_handshake_receipt)
            case "commit":
                required = (
                    *shared,
                    self.binding_handshake_receipt,
                    self.activation_reserve_receipt,
                    self.binding_finalize_receipt,
                )
            case "reprepare":
                required = (
                    self.attestation_generation,
                    self.failed_reservation_receipt,
                    self.previous_attestation_receipt,
                    self.activation_evidence_receipt,
                )
            case "restore":
                required = (
                    self.failed_reservation_receipt,
                    self.binding_restore_receipt,
                    self.restore_verification_receipt,
                )
            case unreachable:
                assert_never(unreachable)
        if any(value is None for value in required):
            error_code = f"{self.phase}_arguments_incomplete"
            raise ValueError(error_code)
        return self


class SearchProjection(ClosedModel):
    actual_production_title_body_utf8_bytes: int = Field(ge=0)
    fixture_floor_bytes: int = Field(ge=0)
    raw_measured_amplification: float = Field(ge=0)
    raw_added_bytes: int = Field(ge=0)
    inflated_added_bytes: int = Field(ge=0)
    inflation_count: Literal[1]


class DimensionProjection(ClosedModel):
    name: str = Field(min_length=1)
    observed_usage: int = Field(ge=0)
    added_usage_raw: int = Field(ge=0)
    added_usage_inflated: int = Field(ge=0)
    inflation_count: Literal[1]
    quota: int = Field(gt=0)
    numerator: int = Field(ge=0)
    ratio: float = Field(ge=0, lt=0.7)
    accepted: Literal[True]


class FreeTierResult(ClosedModel):
    schema_name: Literal["free-tier.result.v1"] = Field(alias="schema")
    accepted: Literal[True]
    phase: Literal["pre-0010", "post-0010"]
    reviewed_sha: Sha
    db_now: str
    manifest_sha256: Sha256
    measurements_sha256: Sha256
    production_measurements_sha256: Sha256
    search_projection: SearchProjection
    dimensions: tuple[DimensionProjection, ...]
    expected_plan_sha256: Sha256 | None = None
    activation_nonce: UUID | None = None
    predecessor_receipt: str | None = None
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def verify_receipt_hash(self) -> FreeTierResult:
        body = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"receipt_sha256"},
        )
        if not hmac.compare_digest(self.receipt_sha256, hash_document(body)):
            error_code = "free_tier_receipt_hash_mismatch"
            raise ValueError(error_code)
        return self


class ChainReceipt(ClosedModel):
    schema_version: Literal[1]
    command: str = Field(min_length=1)
    accepted: bool
    activation_nonce: UUID
    reviewed_sha: Sha
    state_after: ActivationTransition
    payload_sha256: Sha256 | None = None
    cadence_anchor_at: str | None = None
    attestation_generation: int | None = Field(default=None, ge=1)
    attestation_sha256: Sha256 | None = None
    reason: str | None = None
    predecessor_receipt_sha256: Sha256 | None
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def verify_receipt_hash(self) -> ChainReceipt:
        body = self.model_dump(mode="json", exclude={"receipt_sha256"})
        if not hmac.compare_digest(self.receipt_sha256, hash_document(body)):
            error_code = "chain_receipt_hash_mismatch"
            raise ValueError(error_code)
        return self


class ActivationOutput(ClosedModel):
    schema_version: Literal[1] = 1
    command: Literal[
        "activation-reserve",
        "activation-commit",
        "activation-reprepare",
        "activation-restore",
    ]
    accepted: bool
    activation_nonce: UUID
    reviewed_sha: Sha
    db_now: str
    state_before: ActivationTransition
    state_after: ActivationTransition
    attestation_generation: int = Field(ge=1)
    attestation_sha256: Sha256
    predecessor_receipt_sha256: Sha256
    cadence_anchor_at: str | None = None
    reason: str | None = None
    receipt_sha256: Sha256


def canonical_bytes(model: BaseModel, *, exclude_receipt: bool = False) -> bytes:
    """Return deterministic UTF-8 JSON for a parsed receipt."""
    excluded = {"receipt_sha256"} if exclude_receipt else None
    return json.dumps(
        model.model_dump(mode="json", by_alias=True, exclude=excluded),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def hash_document(document: Mapping[str, JsonValue]) -> str:
    """Hash one JSON-compatible schema-closed document."""
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return sha256(encoded).hexdigest()


def load_receipt[ParsedModel: BaseModel](
    path: Path,
    model: type[ParsedModel],
) -> ParsedModel:
    """Load a bounded canonical receipt through its exact Pydantic schema."""
    raw = path.read_bytes()
    if len(raw) > MAX_RECEIPT_BYTES:
        error_code = "receipt_oversize"
        raise ValueError(error_code)
    parsed = model.model_validate_json(raw)
    if raw.removesuffix(b"\n") != canonical_bytes(parsed):
        error_code = "receipt_noncanonical"
        raise ValueError(error_code)
    return parsed


def write_output(path: Path, output: ActivationOutput) -> None:
    """Create one immutable canonical redacted activation receipt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(canonical_bytes(output) + b"\n")


__all__ = (
    "ActivateRequest",
    "ActivationOutput",
    "ChainReceipt",
    "FreeTierResult",
    "canonical_bytes",
    "hash_document",
    "load_receipt",
    "write_output",
)
