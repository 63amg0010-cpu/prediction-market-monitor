"""Schema-closed public receipts for release privacy incidents."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from scripts.release_privacy_contracts import ClosedModel, Sha256, ViolationKind


class ContainmentReceipt(ClosedModel):
    """Public-safe proof that the affected source is inert."""

    schema_version: Literal[1] = 1
    command: Literal["privacy-contain"] = "privacy-contain"
    accepted: Literal[True] = True
    status: Literal["CONTAINED"] = "CONTAINED"
    scope_sha256: Sha256
    predecessor_sha256: Sha256
    mutation_sha256: Sha256
    frozen_graph_sha256: Sha256
    frozen_target_count: int = Field(ge=0)


class PurgeReceipt(ClosedModel):
    """Public-safe proof of database and GitHub deletion."""

    schema_version: Literal[1] = 1
    command: Literal["privacy-purge"] = "privacy-purge"
    accepted: Literal[True] = True
    status: Literal["PURGED"] = "PURGED"
    scope_sha256: Sha256
    predecessor_sha256: Sha256
    containment_sha256: Sha256
    mutation_sha256: Sha256
    frozen_graph_sha256: Sha256
    deleted_row_count: int = Field(ge=0)
    deleted_github_object_count: int = Field(ge=0)
    github_disposition_sha256: Sha256


class MatrixBProof(ClosedModel):
    """Terminal Matrix-P rollback chain that intentionally is not restored."""

    command: Literal["matrix-b-terminal-chain"]
    accepted: Literal[True]
    incident_class: ViolationKind
    durable_state: Literal["restore_writing"]
    database_revision: Literal["20260727_0010"]
    receipt_sha256: Sha256
    health_sha256: Sha256


class PrivacyVerifyReceipt(ClosedModel):
    """Final restored receipt or fail-closed retention HOLD."""

    schema_version: Literal[1] = 1
    command: Literal["privacy-verify"] = "privacy-verify"
    accepted: bool
    status: Literal["RESTORED", "PRIVACY_HOLD"]
    scope_sha256: Sha256
    predecessor_sha256: Sha256
    containment_sha256: Sha256
    purge_sha256: Sha256
    matrix_b_sha256: Sha256
    durable_state: Literal["restore_writing", "restored"]
    database_verification_sha256: Sha256
    github_verification_sha256: Sha256
    static_scan_sha256: Sha256
    provider_log_disposition_sha256: Sha256
    restore_mutation_sha256: Sha256 | None = None
    hold_reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def terminal_status_matches_acceptance(self) -> Self:
        """Prevent a HOLD from claiming restored, or vice versa."""
        restored = self.accepted and self.status == "RESTORED"
        if restored != (self.durable_state == "restored"):
            msg = "only accepted privacy-verify may report restored"
            raise ValueError(msg)
        if restored != (self.restore_mutation_sha256 is not None):
            msg = "restored receipt requires the terminal mutation hash"
            raise ValueError(msg)
        if restored == bool(self.hold_reasons):
            msg = "hold reasons must exist exactly for PRIVACY_HOLD"
            raise ValueError(msg)
        return self


__all__ = (
    "ContainmentReceipt",
    "MatrixBProof",
    "PrivacyVerifyReceipt",
    "PurgeReceipt",
)
