"""Shared fail-closed state for concrete privacy runtime adapters."""

# ruff: noqa: EM101, EM102, PLR2004

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from scripts.release_privacy_contracts import IncidentScope

Component = Literal["database", "github", "provider"]
_REQUIRED: frozenset[Component] = frozenset({"database", "github", "provider"})


class PrivacyRuntimeError(RuntimeError):
    """Stable privacy HOLD whose text never contains protected values."""

    def __init__(self, code: str) -> None:
        """Create one redacted fail-closed error."""
        super().__init__(f"PRIVACY_HOLD: {code}")


def digest(value: object) -> str:
    """Hash a JSON-compatible projection without returning its raw values."""
    raw = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def scope_digest(scope: IncidentScope) -> str:
    """Hash the exact protected incident identity."""
    return digest(
        {
            "activation_nonce": scope.activation_nonce,
            "epoch_id": scope.epoch_id,
            "plan": scope.approved_plan_sha256,
            "reviewed_sha": scope.reviewed_sha,
            "source_id": scope.source_id,
            "violation_kind": scope.violation_kind,
        }
    )


@dataclass(slots=True)
class PrivacyProofSession:
    """In-memory, hash-only authorization for the terminal restore CAS."""

    _scope_sha256: str | None = None
    _proofs: dict[Component, str] = field(default_factory=dict)

    def record(
        self,
        component: Component,
        scope: IncidentScope,
        proof_sha256: str,
        *,
        accepted: bool,
    ) -> None:
        """Record only an accepted component digest for one exact scope."""
        if not accepted or len(proof_sha256) != 64:
            raise PrivacyRuntimeError(f"{component}_proof_incomplete")
        current = scope_digest(scope)
        if self._scope_sha256 not in {None, current}:
            raise PrivacyRuntimeError("proof_scope_mismatch")
        self._scope_sha256 = current
        self._proofs[component] = proof_sha256

    def require_complete(self, scope: IncidentScope) -> tuple[str, ...]:
        """Reject direct/ordinary restore paths lacking all privacy proofs."""
        if self._scope_sha256 != scope_digest(scope):
            raise PrivacyRuntimeError("privacy_proof_missing")
        if frozenset(self._proofs) != _REQUIRED:
            raise PrivacyRuntimeError("privacy_proof_incomplete")
        return tuple(self._proofs[name] for name in sorted(_REQUIRED))


__all__ = (
    "PrivacyProofSession",
    "PrivacyRuntimeError",
    "digest",
    "scope_digest",
)
