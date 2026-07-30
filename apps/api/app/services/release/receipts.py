"""RFC 8785-compatible, schema-closed release receipt primitives."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from hashlib import sha256
from typing import Annotated, ClassVar, Literal, cast
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
JsonScalar = str | int | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
MAX_RECEIPT_BYTES = 65_536
MAX_SAFE_INTEGER = 9_007_199_254_740_991
SURROGATE_MIN = 0xD800
SURROGATE_MAX = 0xDFFF
ReceiptDatetime = datetime
ReceiptUuid = UUID


class ClosedReceiptModel(BaseModel):
    """Immutable Pydantic boundary that rejects unknown receipt fields."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


class ReceiptDatabaseTimestamps(ClosedReceiptModel):
    """Database-owned timestamps carried by a release chain node."""

    created_at_db: ReceiptDatetime
    reserved_at_db: ReceiptDatetime | None = None
    selection_floor_at: ReceiptDatetime | None = None
    claimed_at_db: ReceiptDatetime | None = None


class ReleaseChainReceipt[CommandName: str](ClosedReceiptModel):
    """Common identity and outcome fields shared by every new release receipt."""

    schema_name: Literal["release-chain-receipt.v1"] = Field(alias="schema")
    command: CommandName
    reviewed_sha: Sha
    approved_plan_sha256: Sha256
    approval_round_id: Sha256
    approval_launch_sha256s: tuple[Sha256, Sha256]
    activation_nonce: ReceiptUuid
    dispatch_nonce: ReceiptUuid | None
    attempt: int = Field(ge=0)
    database_timestamps: ReceiptDatabaseTimestamps
    accepted: bool
    terminal_for_attempt: bool
    retry_permitted: bool
    predecessor_receipt_sha256: Sha256 | None
    receipt_sha256: Sha256

    @model_validator(mode="after")
    def verify_hash_and_outcome(self) -> ReleaseChainReceipt[CommandName]:
        """Reject contradictory outcomes and any mutated receipt hash."""
        if self.accepted and self.retry_permitted:
            error_code = "accepted_receipt_cannot_retry"
            raise ValueError(error_code)
        if self.retry_permitted and not self.terminal_for_attempt:
            error_code = "retry_requires_terminal_attempt"
            raise ValueError(error_code)
        body = self.model_dump(
            mode="json", by_alias=True, exclude={"receipt_sha256"}
        )
        expected = sha256(canonicalize(body)).hexdigest()
        if not hmac.compare_digest(self.receipt_sha256, expected):
            error_code = "receipt_hash_mismatch"
            raise ValueError(error_code)
        return self


def canonicalize(value: object) -> bytes:
    """Encode the receipt JSON subset using RFC 8785 member ordering."""
    return _encode(value).encode("utf-8")


def _encode(value: object) -> str:
    if value is None or isinstance(value, (bool, str, int, float)):
        return _encode_scalar(value)
    if isinstance(value, Mapping):
        return _encode_mapping(cast("Mapping[object, object]", value))
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return "[" + ",".join(_encode(item) for item in value) + "]"
    error_code = "canonical_json_type_invalid"
    raise TypeError(error_code)


def _encode_scalar(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        _reject_surrogates(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            error_code = "canonical_integer_out_of_range"
            raise ValueError(error_code)
        return str(value)
    if isinstance(value, float):
        error_code = "canonical_float_not_supported"
        raise TypeError(error_code)
    error_code = "canonical_scalar_type_invalid"
    raise TypeError(error_code)


def _encode_mapping(value: Mapping[object, object]) -> str:
    members: list[str] = []
    for key in sorted(value, key=_utf16_sort_key):
        if not isinstance(key, str):
            error_code = "canonical_object_key_invalid"
            raise TypeError(error_code)
        members.append(f"{_encode(key)}:{_encode(value[key])}")
    return "{" + ",".join(members) + "}"


def _utf16_sort_key(value: object) -> bytes:
    if not isinstance(value, str):
        error_code = "canonical_object_key_invalid"
        raise TypeError(error_code)
    _reject_surrogates(value)
    return value.encode("utf-16be")


def _reject_surrogates(value: str) -> None:
    if any(
        SURROGATE_MIN <= ord(character) <= SURROGATE_MAX for character in value
    ):
        error_code = "canonical_string_invalid"
        raise ValueError(error_code)


def verify_canonical_receipt[ParsedReceipt: BaseModel](
    raw: bytes,
    model: type[ParsedReceipt],
) -> ParsedReceipt:
    """Parse, canonical-byte-check, and hash-check a bounded receipt."""
    if len(raw) > MAX_RECEIPT_BYTES:
        error_code = "receipt_oversize"
        raise ValueError(error_code)
    try:
        document = cast(
            "object",
            json.loads(raw, object_pairs_hook=_unique_object),
        )
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        error_code = "receipt_json_invalid"
        raise ValueError(error_code) from error
    if raw != canonicalize(document):
        error_code = "receipt_noncanonical"
        raise ValueError(error_code)
    return model.model_validate(document)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            error_code = "receipt_duplicate_key"
            raise ValueError(error_code)
        result[key] = value
    return result


__all__ = (
    "ClosedReceiptModel",
    "ReceiptDatabaseTimestamps",
    "ReleaseChainReceipt",
    "Sha",
    "Sha256",
    "canonicalize",
    "verify_canonical_receipt",
)
