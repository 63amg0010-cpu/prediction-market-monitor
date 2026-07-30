"""Deterministic retained-state operations for migration 0011."""

import hashlib
import json
from typing import ClassVar, Final
from uuid import UUID, uuid5

from alembic import op
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from scripts.activation_migration_evidence import (
    RETAINED_STATE_CONFLICT,
    load_evidence,
    reject,
)

REPOSITORY_UUID_NAMESPACE: Final = UUID("cd932162-ffd4-5bb4-b027-cbdb38a789d3")
MANIFOLD_SOURCE_ID: Final = UUID("0890756a-ca23-5697-ae4c-0de527361064")
MANIFOLD_SCOPE_VERSION: Final = "phase1-reviewed-v1"


class _LatestTransition(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")

    activation_nonce: UUID
    source_id: UUID
    attestation_id: UUID | None
    id: UUID


def prepare_source() -> None:
    """Insert or byte-verify the disabled source, attestation, and preparation."""
    attestation, canonical, attestation_sha, receipt_sha = load_evidence(
        MANIFOLD_SCOPE_VERSION
    )
    attestation_id = uuid5(
        REPOSITORY_UUID_NAMESPACE,
        f"attestation:{attestation.activation_nonce}:{attestation.attestation_generation}:{attestation_sha}",
    )
    transition_id = uuid5(
        REPOSITORY_UUID_NAMESPACE,
        f"transition:{attestation.activation_nonce}:{attestation.attestation_generation}:prepared:{receipt_sha}",
    )
    bind = op.get_bind()
    parameters = {
        "activation_nonce": attestation.activation_nonce,
        "attestation": canonical,
        "attestation_id": attestation_id,
        "attestation_sha": attestation_sha,
        "authorization_sha": attestation.authorization_evidence_sha256,
        "database_time": attestation.evidence_database_time,
        "free_tier_sha": attestation.free_tier_evidence_sha256,
        "generation": attestation.attestation_generation,
        "predecessor_sha": attestation.predecessor_attestation_sha256,
        "provenance_sha": attestation.provenance_sha256,
        "receipt_sha": receipt_sha,
        "reviewed_sha": attestation.reviewed_sha,
        "source_id": MANIFOLD_SOURCE_ID,
        "transition_id": transition_id,
    }
    _ = bind.execute(
        text(
            """
            INSERT INTO community_sources (
                id, country, platform, external_key, display_name, scope_version,
                enabled, active_authorization_id, current_budget_id,
                current_binding_id, current_cadence_id
            ) VALUES (
                :source_id, 'us', 'manifold', 'manifold-comments',
                'Manifold reviewed public comments', 'phase1-reviewed-v1',
                false, NULL, NULL, NULL, NULL
            ) ON CONFLICT (platform, external_key, scope_version) DO NOTHING
            """
        ),
        parameters,
    )
    immutable = bind.execute(
        text(
            """
            SELECT id, country::text, display_name, enabled,
                   active_authorization_id, current_budget_id,
                   current_binding_id, current_cadence_id
            FROM community_sources
            WHERE platform = 'manifold' AND external_key = 'manifold-comments'
              AND scope_version = 'phase1-reviewed-v1'
            """
        )
    ).one()
    if immutable != (
        MANIFOLD_SOURCE_ID,
        "us",
        "Manifold reviewed public comments",
        False,
        None,
        None,
        None,
        None,
    ):
        reject(RETAINED_STATE_CONFLICT)
    for statement in (
        """
        INSERT INTO source_activation_attestations (
            id, source_id, activation_nonce, attestation_generation,
            attestation_sha256, canonical_attestation, reviewed_sha,
            predecessor_attestation_sha256, authorization_evidence_sha256,
            free_tier_evidence_sha256, provenance_sha256, evidence_database_time
        ) VALUES (
            :attestation_id, :source_id, :activation_nonce, :generation,
            :attestation_sha, :attestation, :reviewed_sha, :predecessor_sha,
            :authorization_sha, :free_tier_sha, :provenance_sha, :database_time
        ) ON CONFLICT (activation_nonce, attestation_generation, attestation_sha256)
          DO NOTHING
        """,
        """
        INSERT INTO source_activation_state_transitions (
            id, activation_nonce, source_id, attestation_id, state,
            receipt_sha256
        ) VALUES (
            :transition_id, :activation_nonce, :source_id, :attestation_id,
            'prepared', :receipt_sha
        ) ON CONFLICT (activation_nonce, receipt_sha256) DO NOTHING
        """,
    ):
        _ = bind.execute(text(statement), parameters)
    persisted = bind.execute(
        text(
            """
            SELECT source_id, activation_nonce, attestation_generation,
                   attestation_sha256, canonical_attestation, reviewed_sha,
                   predecessor_attestation_sha256,
                   authorization_evidence_sha256,
                   free_tier_evidence_sha256, provenance_sha256,
                   evidence_database_time
            FROM source_activation_attestations
            WHERE id = :attestation_id
            """
        ),
        parameters,
    ).one()
    expected = (
        MANIFOLD_SOURCE_ID,
        attestation.activation_nonce,
        attestation.attestation_generation,
        attestation_sha,
        canonical,
        attestation.reviewed_sha,
        attestation.predecessor_attestation_sha256,
        attestation.authorization_evidence_sha256,
        attestation.free_tier_evidence_sha256,
        attestation.provenance_sha256,
        attestation.evidence_database_time,
    )
    if persisted != expected:
        reject(RETAINED_STATE_CONFLICT)


def append_deactivated_transition() -> None:
    """Append one deterministic inert transition after pointer unlinking."""
    bind = op.get_bind()
    raw_latest = (
        bind.execute(
            text(
                """
            SELECT activation_nonce, source_id, attestation_id, id
            FROM source_activation_state_transitions
            WHERE source_id = :source_id
            ORDER BY transition_at_db DESC, id DESC
            LIMIT 1
            """
            ),
            {"source_id": MANIFOLD_SOURCE_ID},
        )
        .mappings()
        .one_or_none()
    )
    if raw_latest is None:
        return
    latest = _LatestTransition.model_validate(raw_latest)
    receipt = json.dumps(
        {
            "activation_nonce": str(latest.activation_nonce),
            "command": "0011-technical-downgrade",
            "predecessor_transition_id": str(latest.id),
            "schema_version": 1,
            "source_id": str(latest.source_id),
            "state": "deactivated",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    receipt_sha = hashlib.sha256(receipt).hexdigest()
    transition_id = uuid5(
        REPOSITORY_UUID_NAMESPACE,
        f"transition:{latest.activation_nonce}:deactivated:{receipt_sha}",
    )
    _ = bind.execute(
        text(
            """
            INSERT INTO source_activation_state_transitions (
                id, activation_nonce, source_id, attestation_id,
                predecessor_transition_id, state, receipt_sha256
            ) VALUES (
                :id, :activation_nonce, :source_id, :attestation_id,
                :predecessor_id, 'deactivated', :receipt_sha
            ) ON CONFLICT (activation_nonce, receipt_sha256) DO NOTHING
            """
        ),
        {
            "activation_nonce": latest.activation_nonce,
            "attestation_id": latest.attestation_id,
            "id": transition_id,
            "predecessor_id": latest.id,
            "receipt_sha": receipt_sha,
            "source_id": latest.source_id,
        },
    )


__all__ = ("append_deactivated_transition", "prepare_source")
