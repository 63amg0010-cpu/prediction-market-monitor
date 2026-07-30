"""Locked PostgreSQL state loading for source activation."""

# ruff: noqa: TC003

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import TextClause, text

from .source_activation import MANIFOLD_SOURCE_ID
from .source_activation_domain import ActivationState, ActivationTransition

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncConnection

CURRENT_STATE: Final[TextClause] = text(
    """
    SELECT transition.id AS transition_id,
           transition.activation_nonce,
           transition.state,
           transition.attestation_id,
           transition.binding_intent_id,
           transition.current_cadence_id AS cadence_id,
           attestation.attestation_generation,
           attestation.attestation_sha256,
           attestation.prepared_at,
           intent.payload_sha256 AS binding_payload_sha256,
           source.enabled AS source_enabled,
           source.active_authorization_id,
           source.current_budget_id,
           source.current_binding_id,
           source.current_cadence_id,
           EXISTS (
               SELECT 1 FROM source_activation_state_transitions written
               WHERE written.activation_nonce = transition.activation_nonce
                 AND written.attestation_id = transition.attestation_id
                 AND written.state IN (
                     'binding_writing', 'binding_committed',
                     'github_finalized', 'active'
                 )
           ) AS binding_write_occurred
    FROM source_activation_state_transitions transition
    JOIN source_activation_attestations attestation
      ON attestation.id = transition.attestation_id
    JOIN community_sources source ON source.id = transition.source_id
    LEFT JOIN source_binding_change_intents intent
      ON intent.id = transition.binding_intent_id
    WHERE transition.source_id = :source_id
    ORDER BY transition.transition_at_db DESC, transition.id DESC
    LIMIT 1
    FOR UPDATE OF transition, source
    """
)


class _CurrentStateRow(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    transition_id: UUID
    activation_nonce: UUID
    state: ActivationTransition
    attestation_id: UUID
    binding_intent_id: UUID
    cadence_id: UUID | None
    attestation_generation: int
    attestation_sha256: str
    prepared_at: datetime
    binding_payload_sha256: str
    source_enabled: bool
    active_authorization_id: UUID | None
    current_budget_id: UUID | None
    current_binding_id: UUID | None
    current_cadence_id: UUID | None
    binding_write_occurred: bool


@dataclass(frozen=True, slots=True)
class LockedActivationState:
    """Current transition plus identities required by one atomic write."""

    state: ActivationState
    transition_id: UUID
    attestation_id: UUID
    binding_intent_id: UUID
    binding_payload_sha256: str
    cadence_id: UUID | None


async def load_current_state(connection: AsyncConnection) -> LockedActivationState:
    """Lock and parse the latest retained Manifold activation state."""
    row = (
        (await connection.execute(CURRENT_STATE, {"source_id": MANIFOLD_SOURCE_ID}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        error_code = "activation_state_missing"
        raise RuntimeError(error_code)
    parsed = _CurrentStateRow.model_validate(row)
    return LockedActivationState(
        state=ActivationState(
            activation_nonce=parsed.activation_nonce,
            attestation_generation=parsed.attestation_generation,
            attestation_sha256=parsed.attestation_sha256,
            prepared_at=parsed.prepared_at,
            state=parsed.state,
            source_enabled=parsed.source_enabled,
            active_authorization_id=parsed.active_authorization_id,
            current_budget_id=parsed.current_budget_id,
            current_binding_id=parsed.current_binding_id,
            current_cadence_id=parsed.current_cadence_id,
            binding_write_occurred=parsed.binding_write_occurred,
            restore_verified=parsed.state == "restore_writing",
        ),
        transition_id=parsed.transition_id,
        attestation_id=parsed.attestation_id,
        binding_intent_id=parsed.binding_intent_id,
        binding_payload_sha256=parsed.binding_payload_sha256,
        cadence_id=parsed.cadence_id,
    )


__all__ = ("CURRENT_STATE", "LockedActivationState", "load_current_state")
