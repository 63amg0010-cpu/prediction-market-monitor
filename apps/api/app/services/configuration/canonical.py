"""Canonical JSON serialization and SHA-256 identity helpers."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Final, TypeGuard

from pydantic import BaseModel, TypeAdapter

CANONICALIZATION: Final[str] = "json-sort-keys-nfc-v1"


type CanonicalInput = (
    str
    | int
    | float
    | bool
    | None
    | Mapping[str, "CanonicalInput"]
    | Sequence["CanonicalInput"]
)

_CANONICAL_ADAPTER: Final[TypeAdapter[CanonicalInput]] = TypeAdapter(CanonicalInput)


def _is_mapping(value: CanonicalInput) -> TypeGuard[Mapping[str, CanonicalInput]]:
    return isinstance(value, Mapping)


def _is_sequence(value: CanonicalInput) -> TypeGuard[Sequence[CanonicalInput]]:
    return isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    )


def _normalize(value: CanonicalInput) -> CanonicalInput:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if _is_mapping(value):
        return {
            unicodedata.normalize("NFC", str(key)): _normalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if _is_sequence(value):
        return [_normalize(item) for item in value]
    return value


def canonical_bytes(model: BaseModel) -> bytes:
    """Serialize a Pydantic model as stable UTF-8 JSON bytes."""
    validated = _CANONICAL_ADAPTER.validate_python(
        model.model_dump(mode="json", by_alias=True)
    )
    normalized = _normalize(validated)
    return json.dumps(
        normalized,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(model: BaseModel) -> str:
    """Hash canonical model bytes with lowercase SHA-256."""
    return sha256(canonical_bytes(model)).hexdigest()
