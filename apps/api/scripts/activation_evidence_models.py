"""Schema-closed activation evidence shared by the API and 0011 migration."""

import sys
from datetime import datetime
from typing import Annotated, ClassVar, Literal, Self
from uuid import UUID

import orjson
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError, ValidationError

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ActivationEvidenceModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class PublicActivationAttestation(ActivationEvidenceModel):
    """Public, redacted evidence accepted by the activation boundary."""

    schema_version: Literal[1]
    reviewed_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    activation_nonce: UUID
    attestation_generation: int = Field(ge=1)
    source_scope_version: str = Field(min_length=1, max_length=80)
    authorization_evidence_sha256: Sha256
    free_tier_evidence_sha256: Sha256
    provenance_sha256: Sha256
    predecessor_attestation_sha256: Sha256 | None = None
    captured_at: datetime
    evidence_database_time: datetime
    public_evidence_urls: tuple[HttpUrl, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def require_aware_times(self) -> Self:
        """Reject local or ambiguous timestamps at the public trust boundary."""
        timezone_missing = (
            self.captured_at.tzinfo is None
            or self.evidence_database_time.tzinfo is None
        )
        if timezone_missing:
            error_code = "activation_evidence_timezone_required"
            raise PydanticCustomError(
                error_code,
                "activation evidence timestamps must include a timezone",
            )
        return self


def canonical_attestation_bytes(attestation: PublicActivationAttestation) -> bytes:
    """Return the normalized bytes hashed by workflow, API, and migration."""
    return orjson.dumps(
        attestation.model_dump(mode="json"),
        option=orjson.OPT_SORT_KEYS,
    )


class ActivationEvidenceVerifyRequest(ActivationEvidenceModel):
    """Exact workflow run and reservation bound to one public attestation."""

    attestation: PublicActivationAttestation
    attestation_sha256: Sha256
    reservation_receipt_sha256: Sha256
    dispatch_nonce: UUID
    attempt: Literal[1, 2]
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class ActivationEvidenceReceipt(ActivationEvidenceModel):
    """Schema-closed public receipt returned by the read-only verifier."""

    schema_version: Literal[1] = 1
    accepted: Literal[True] = True
    activation_nonce: UUID
    attestation_generation: int = Field(ge=1)
    attestation_sha256: Sha256
    reservation_receipt_sha256: Sha256
    dispatch_nonce: UUID
    attempt: Literal[1, 2]
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    head_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    database_time: datetime


def canonical_evidence_receipt_bytes(receipt: ActivationEvidenceReceipt) -> bytes:
    """Return the normalized bytes consumed by migration 0011."""
    return orjson.dumps(
        receipt.model_dump(mode="json"),
        option=orjson.OPT_SORT_KEYS,
    )


def main() -> int:
    """Canonicalize one schema-closed activation document from standard input."""
    arguments = sys.argv[1:]
    if len(arguments) > 1:
        _ = sys.stderr.write("activation evidence rejected\n")
        return 2
    kind = arguments[0] if arguments else "attestation"
    try:
        raw = sys.stdin.buffer.read()
        if kind == "attestation":
            canonical = canonical_attestation_bytes(
                PublicActivationAttestation.model_validate_json(raw)
            )
        elif kind == "receipt":
            canonical = canonical_evidence_receipt_bytes(
                ActivationEvidenceReceipt.model_validate_json(raw)
            )
        else:
            _ = sys.stderr.write("activation evidence rejected\n")
            return 2
    except ValidationError:
        _ = sys.stderr.write("activation evidence rejected\n")
        return 2
    _ = sys.stdout.buffer.write(canonical)
    return 0


__all__ = (
    "ActivationEvidenceReceipt",
    "ActivationEvidenceVerifyRequest",
    "PublicActivationAttestation",
    "canonical_attestation_bytes",
    "canonical_evidence_receipt_bytes",
)


if __name__ == "__main__":
    raise SystemExit(main())
