"""PostgreSQL writes for the reviewed Manifold activation state machine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final
from uuid import UUID

from . import _source_activation_sql as sql

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncConnection

MANIFOLD_SOURCE_ID: Final = UUID("0890756a-ca23-5697-ae4c-0de527361064")


@dataclass(frozen=True, slots=True)
class TransitionIdentity:
    """Immutable transition provenance shared by every write."""

    activation_nonce: UUID
    attestation_id: UUID
    binding_intent_id: UUID
    predecessor_transition_id: UUID
    receipt_sha256: str
    transition_id: UUID


@dataclass(frozen=True, slots=True)
class ReserveWrite:
    """Cadence reservation values derived from the database clock."""

    identity: TransitionIdentity
    db_now: datetime
    cadence_id: UUID
    cadence_anchor_at: datetime
    expires_at: datetime
    recheck_at: datetime


@dataclass(frozen=True, slots=True)
class CommitWrite:
    """Authorization, budget, and pointer values for activation commit."""

    identity: TransitionIdentity
    db_now: datetime
    cadence_id: UUID
    authorization_id: UUID
    budget_id: UUID
    authorization_evidence_sha256: str
    budget_evidence_sha256: str
    effective_at: datetime
    expires_at: datetime
    accepted: bool


@dataclass(frozen=True, slots=True)
class ReprepareWrite:
    """Fresh attestation and rotated intent values after a failed reservation."""

    identity: TransitionIdentity
    db_now: datetime
    attestation_id: UUID
    generation: int
    attestation_sha256: str
    canonical_attestation: bytes
    reviewed_sha: str
    authorization_sha256: str
    free_tier_sha256: str
    provenance_sha256: str
    evidence_database_time: datetime
    prepared_at: datetime
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class RestoreWrite:
    """Two nonterminal restore transitions with distinct receipts."""

    identity: TransitionIdentity
    db_now: datetime
    deactivated_id: UUID
    deactivated_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class TransitionPointers:
    """Optional current pointers carried only by active transitions."""

    authorization_id: UUID | None = None
    budget_id: UUID | None = None
    cadence_id: UUID | None = None


async def database_now_locked(connection: AsyncConnection) -> datetime:
    """Acquire the transaction lock and read its timestamp exactly once."""
    _ = await connection.execute(sql.ADVISORY_LOCK)
    value = await connection.scalar(sql.DATABASE_NOW)
    if value is None:
        error_code = "database_time_invalid"
        raise TypeError(error_code)
    return value


async def persist_reserve(connection: AsyncConnection, value: ReserveWrite) -> None:
    """Insert an idempotent cadence epoch and anchor-reserved transition."""
    parameters = {
        **_identity_parameters(value.identity),
        "anchor": value.cadence_anchor_at,
        "cadence_id": value.cadence_id,
        "db_now": value.db_now,
        "expires": value.expires_at,
        "recheck": value.recheck_at,
    }
    _ = await connection.execute(sql.INSERT_CADENCE, parameters)
    await _insert_transition(
        connection,
        parameters,
        state="anchor_reserved",
        pointers=TransitionPointers(cadence_id=value.cadence_id),
    )


async def persist_commit(connection: AsyncConnection, value: CommitWrite) -> None:
    """Atomically fail a cutoff miss or create and link an active scope."""
    parameters = {
        **_identity_parameters(value.identity),
        "authorization_id": value.authorization_id,
        "authorization_sha": value.authorization_evidence_sha256,
        "budget_id": value.budget_id,
        "budget_sha": value.budget_evidence_sha256,
        "cadence_id": value.cadence_id,
        "db_now": value.db_now,
        "effective": value.effective_at,
        "expires": value.expires_at,
        "scope": json.dumps(_manifold_scope(), separators=(",", ":"), sort_keys=True),
    }
    if not value.accepted:
        await _insert_transition(connection, parameters, state="failed")
        return
    _ = await connection.execute(sql.INSERT_AUTHORIZATION, parameters)
    _ = await connection.execute(sql.INSERT_BUDGET, parameters)
    result = await connection.execute(sql.ACTIVATE_SOURCE, parameters)
    if result.rowcount != 1:
        error_code = "activation_source_raced"
        raise RuntimeError(error_code)
    await _insert_transition(
        connection,
        parameters,
        state="active",
        pointers=TransitionPointers(
            authorization_id=value.authorization_id,
            budget_id=value.budget_id,
            cadence_id=value.cadence_id,
        ),
    )


async def persist_reprepare(
    connection: AsyncConnection,
    value: ReprepareWrite,
) -> None:
    """Append fresh evidence and rotate the one durable intent."""
    parameters = {
        **_identity_parameters(value.identity),
        "attestation": value.canonical_attestation,
        "attestation_id": value.attestation_id,
        "attestation_sha": value.attestation_sha256,
        "authorization_sha": value.authorization_sha256,
        "db_now": value.db_now,
        "evidence_time": value.evidence_database_time,
        "free_tier_sha": value.free_tier_sha256,
        "generation": value.generation,
        "payload_sha": value.payload_sha256,
        "prepared_at": value.prepared_at,
        "provenance_sha": value.provenance_sha256,
        "reviewed_sha": value.reviewed_sha,
    }
    _ = await connection.execute(sql.INSERT_ATTESTATION, parameters)
    _ = await connection.execute(sql.ROTATE_INTENT, parameters)
    await _insert_transition(connection, parameters, state="prepared")


async def persist_restore(connection: AsyncConnection, value: RestoreWrite) -> None:
    """Disable and unlink before ending only at restore_writing."""
    parameters = {
        **_identity_parameters(value.identity),
        "db_now": value.db_now,
        "deactivated_id": value.deactivated_id,
        "deactivated_receipt": value.deactivated_receipt_sha256,
    }
    _ = await connection.execute(sql.DISABLE_SOURCE, parameters)
    _ = await connection.execute(sql.INSERT_DEACTIVATED, parameters)
    parameters["predecessor_id"] = value.deactivated_id
    await _insert_transition(connection, parameters, state="restore_writing")


def _identity_parameters(value: TransitionIdentity) -> dict[str, UUID | str]:
    return {
        "binding_id": value.binding_intent_id,
        "nonce": value.activation_nonce,
        "predecessor_id": value.predecessor_transition_id,
        "prior_attestation_id": value.attestation_id,
        "receipt": value.receipt_sha256,
        "source_id": MANIFOLD_SOURCE_ID,
        "transition_id": value.transition_id,
    }


async def _insert_transition(
    connection: AsyncConnection,
    parameters: Mapping[str, UUID | str | datetime | bytes | int],
    *,
    state: str,
    pointers: TransitionPointers | None = None,
) -> None:
    actual_pointers = pointers or TransitionPointers()
    _ = await connection.execute(
        sql.INSERT_TRANSITION,
        {
            **parameters,
            "attestation_id": parameters.get(
                "attestation_id", parameters["prior_attestation_id"]
            ),
            "authorization_id": actual_pointers.authorization_id,
            "budget_id": actual_pointers.budget_id,
            "cadence_id": actual_pointers.cadence_id,
            "current_binding_id": (
                parameters["binding_id"] if state == "active" else None
            ),
            "state": state,
        },
    )


def _manifold_scope() -> dict[str, int | str | list[str]]:
    return {
        "concurrency": 1,
        "permitted_fields": [
            "source_post_id",
            "canonical_url",
            "title",
            "body",
            "published_at",
            "comments_count",
            "upvote_or_score",
        ],
        "permitted_methods": ["GET"],
        "permitted_routes": ["/v0/comments", "/v0/markets"],
        "permitted_subreddits": [],
        "purpose": (
            "personal_noncommercial_prediction_market_monitoring_no_model_training"
        ),
        "requests_per_minute": 30,
    }
