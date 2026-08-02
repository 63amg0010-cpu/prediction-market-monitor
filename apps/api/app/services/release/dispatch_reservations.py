"""Transactional persistence for generic post-0010 dispatch reservations."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from .receipts import MAX_RECEIPT_BYTES, Sha, Sha256, canonicalize
from .workflow_claims import DispatchReservationReceipt

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

RuntimePath = Path

PREDECESSOR_SQL = """
SELECT receipt_sha256, canonical_receipt, reviewed_sha,
       approved_plan_sha256, approval_round_id, approval_launch_sha256s,
       activation_nonce
FROM release_receipt_chain
WHERE receipt_sha256 = :receipt_sha256
FOR SHARE
"""

INSERT_RESERVATION_SQL = """
INSERT INTO release_operation_reservations (
    receipt_sha256, canonical_receipt, predecessor_receipt_sha256,
    reviewed_sha, approved_plan_sha256, approval_round_id,
    approval_launch_sha256s, activation_nonce, dispatch_nonce,
    repository, git_ref, workflow_file, display_title, head_sha,
    event_name, operation, revision, attempt, reserved_at_db, selection_floor_at,
    claimed_run_id, claimed_run_attempt, claimed_at_db
) VALUES (
    :receipt_sha256, :canonical_receipt, :predecessor_receipt_sha256,
    :reviewed_sha, :approved_plan_sha256, :approval_round_id,
    CAST(:approval_launch_sha256s AS jsonb), :activation_nonce,
    :dispatch_nonce, :repository, :git_ref, :workflow, :display_title,
    :head_sha, 'workflow_dispatch', NULL, NULL, :attempt,
    :reserved_at_db, :selection_floor_at,
    NULL, NULL, NULL
)
"""

INSERT_CHAIN_SQL = """
INSERT INTO release_receipt_chain (
    receipt_sha256, canonical_receipt, command, reviewed_sha,
    approved_plan_sha256, approval_round_id, approval_launch_sha256s,
    activation_nonce, dispatch_nonce, attempt, accepted,
    terminal_for_attempt, retry_permitted, predecessor_receipt_sha256,
    created_at_db
) VALUES (
    :receipt_sha256, :canonical_receipt, 'dispatch-reserve', :reviewed_sha,
    :approved_plan_sha256, :approval_round_id,
    CAST(:approval_launch_sha256s AS jsonb), :activation_nonce,
    :dispatch_nonce, :attempt, true, false, false,
    :predecessor_receipt_sha256, :reserved_at_db
)
"""


class DispatchReserveRequest(BaseModel):
    """Validated operator inputs excluding the secret database URL."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    workflow: str = Field(pattern=r"^[A-Za-z0-9_.-]+\.ya?ml$")
    display_title: str = Field(min_length=1, max_length=255)
    head_sha: Sha
    expected_plan_sha256: Sha256
    activation_nonce: UUID
    dispatch_nonce: UUID
    predecessor_receipt: RuntimePath
    attempt: int = Field(gt=0)
    json_out: RuntimePath
    git_ref: str = Field(default="refs/heads/main", pattern=r"^refs/heads/[^\s]+$")
    operation_inputs: dict[str, str] = Field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Predecessor:
    receipt_sha256: str
    reviewed_sha: str
    approved_plan_sha256: str
    approval_round_id: str
    approval_launch_sha256s: tuple[str, str]
    activation_nonce: UUID


async def reserve_dispatch(
    connection: AsyncConnection,
    request: DispatchReserveRequest,
) -> DispatchReservationReceipt:
    """Verify predecessor identity and append one unique reservation."""
    predecessor_sha = _receipt_identifier(request.predecessor_receipt)
    result = await connection.execute(
        text(PREDECESSOR_SQL), {"receipt_sha256": predecessor_sha}
    )
    raw = result.mappings().one_or_none()
    if raw is None:
        error_code = "reservation_predecessor_missing"
        raise ValueError(error_code)
    stored = cast("object", raw["canonical_receipt"])
    supplied = request.predecessor_receipt.read_bytes().removesuffix(b"\n")
    stored_bytes = bytes(stored) if isinstance(stored, (bytes, bytearray)) else None
    if stored_bytes is None or not hmac.compare_digest(stored_bytes, supplied):
        error_code = "reservation_predecessor_bytes_mismatch"
        raise ValueError(error_code)
    predecessor = _predecessor(dict(raw))
    if (
        predecessor.reviewed_sha != request.head_sha
        or predecessor.approved_plan_sha256 != request.expected_plan_sha256
        or predecessor.activation_nonce != request.activation_nonce
    ):
        error_code = "reservation_predecessor_binding_mismatch"
        raise ValueError(error_code)
    db_now = cast(
        "datetime | None",
        await connection.scalar(text("SELECT transaction_timestamp()")),
    )
    floor = cast(
        "datetime | None",
        await connection.scalar(
            text("SELECT date_trunc('second', transaction_timestamp())")
        ),
    )
    if not isinstance(db_now, datetime) or not isinstance(floor, datetime):
        error_code = "reservation_database_time_invalid"
        raise TypeError(error_code)
    receipt = _build_receipt(request, predecessor, db_now, floor)
    values = {
        **receipt.model_dump(mode="python"),
        "canonical_receipt": canonicalize(
            receipt.model_dump(mode="json", by_alias=True)
        ),
        "approval_launch_sha256s": canonicalize(
            list(receipt.approval_launch_sha256s)
        ).decode(),
        "git_ref": request.git_ref,
        "predecessor_receipt_sha256": predecessor.receipt_sha256,
        "reserved_at_db": db_now,
        "selection_floor_at": floor,
    }
    _ = await connection.execute(text(INSERT_RESERVATION_SQL), values)
    _ = await connection.execute(text(INSERT_CHAIN_SQL), values)
    return receipt


def _receipt_identifier(path: RuntimePath) -> str:
    raw = path.read_bytes()
    if len(raw) > MAX_RECEIPT_BYTES:
        error_code = "receipt_oversize"
        raise ValueError(error_code)
    payload = raw.removesuffix(b"\n")
    loaded = cast("object", json.loads(payload))
    if not isinstance(loaded, dict):
        error_code = "receipt_object_required"
        raise TypeError(error_code)
    document = cast("dict[str, object]", loaded)
    if payload != canonicalize(document):
        error_code = "receipt_noncanonical"
        raise ValueError(error_code)
    claimed = document.get("receipt_sha256")
    if claimed is None:
        return sha256(payload).hexdigest()
    if not isinstance(claimed, str):
        error_code = "receipt_identifier_invalid"
        raise TypeError(error_code)
    body = {key: value for key, value in document.items() if key != "receipt_sha256"}
    expected = sha256(canonicalize(body)).hexdigest()
    if not hmac.compare_digest(claimed, expected):
        error_code = "receipt_hash_mismatch"
        raise ValueError(error_code)
    return claimed


def _predecessor(row: dict[str, object]) -> _Predecessor:
    launches = row["approval_launch_sha256s"]
    expected_launch_count = 2
    if not isinstance(launches, list):
        error_code = "reservation_approval_launches_invalid"
        raise TypeError(error_code)
    typed_launches = cast("list[object]", launches)
    if len(typed_launches) != expected_launch_count:
        error_code = "reservation_approval_launches_invalid"
        raise TypeError(error_code)
    return _Predecessor(
        receipt_sha256=str(row["receipt_sha256"]),
        reviewed_sha=str(row["reviewed_sha"]),
        approved_plan_sha256=str(row["approved_plan_sha256"]),
        approval_round_id=str(row["approval_round_id"]),
        approval_launch_sha256s=(str(typed_launches[0]), str(typed_launches[1])),
        activation_nonce=UUID(str(row["activation_nonce"])),
    )


def _build_receipt(
    request: DispatchReserveRequest,
    predecessor: _Predecessor,
    db_now: datetime,
    floor: datetime,
) -> DispatchReservationReceipt:
    body = {
        "schema": "release-chain-receipt.v1",
        "command": "dispatch-reserve",
        "reviewed_sha": request.head_sha,
        "approved_plan_sha256": request.expected_plan_sha256,
        "approval_round_id": predecessor.approval_round_id,
        "approval_launch_sha256s": predecessor.approval_launch_sha256s,
        "activation_nonce": request.activation_nonce,
        "dispatch_nonce": request.dispatch_nonce,
        "attempt": request.attempt,
        "database_timestamps": {
            "created_at_db": db_now,
            "reserved_at_db": db_now,
            "selection_floor_at": floor,
            "claimed_at_db": None,
        },
        "accepted": True,
        "terminal_for_attempt": False,
        "retry_permitted": False,
        "predecessor_receipt_sha256": predecessor.receipt_sha256,
        "repository": request.repository,
        "workflow": request.workflow,
        "display_title": request.display_title,
        "head_sha": request.head_sha,
        "ref": request.git_ref,
        "operation_inputs": request.operation_inputs,
    }
    serialized = DispatchReservationReceipt.model_construct(
        _fields_set=set(body),
        **body,
        receipt_sha256="0" * 64,
    ).model_dump(mode="json", by_alias=True, exclude={"receipt_sha256"})
    return DispatchReservationReceipt.model_validate(
        {**body, "receipt_sha256": sha256(canonicalize(serialized)).hexdigest()}
    )


def write_reservation(
    path: RuntimePath,
    receipt: DispatchReservationReceipt,
) -> None:
    """Write only the committed canonical receipt after transaction success."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_bytes(
        canonicalize(receipt.model_dump(mode="json", by_alias=True)) + b"\n"
    )


__all__ = ("DispatchReserveRequest", "reserve_dispatch", "write_reservation")
