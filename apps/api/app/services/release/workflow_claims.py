"""Schema-closed request and public receipt for a GitHub workflow claim."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from .receipts import ClosedReceiptModel, ReleaseChainReceipt, Sha, Sha256

WorkflowUuid = UUID


class WorkflowDispatchClaimRequest(ClosedReceiptModel):
    """Every public value required to claim one exact durable reservation."""

    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    workflow: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.ya?ml$")
    display_title: str = Field(min_length=1, max_length=255)
    head_sha: Sha
    approved_plan_sha256: Sha256
    activation_nonce: WorkflowUuid
    dispatch_nonce: WorkflowUuid
    reservation_sha256: Sha256
    run_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    event: Literal["workflow_dispatch"]
    ref: Literal["refs/heads/main"]
    environment: str | None = Field(default=None, min_length=1, max_length=100)


class WorkflowDispatchClaimReceipt(
    ReleaseChainReceipt[Literal["workflow-dispatch-claim"]]
):
    """Canonical public evidence returned after the atomic database claim."""

    repository: str
    workflow: str
    display_title: str
    head_sha: Sha
    reservation_sha256: Sha256
    run_id: int
    run_attempt: int
    event: Literal["workflow_dispatch"]
    ref: Literal["refs/heads/main"]
    environment: str | None


class DispatchReservationReceipt(
    ReleaseChainReceipt[Literal["dispatch-reserve"]]
):
    """Canonical reservation created before one workflow dispatch."""

    repository: str
    workflow: str
    display_title: str
    head_sha: Sha
    ref: Literal["refs/heads/main"]
    operation_inputs: dict[str, str]


__all__ = (
    "DispatchReservationReceipt",
    "WorkflowDispatchClaimReceipt",
    "WorkflowDispatchClaimRequest",
)
