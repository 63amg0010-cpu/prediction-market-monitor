"""Canonical 0011 activation-state schema statements."""

from typing import Final

SCHEMA_SQL: Final = """
CREATE TABLE IF NOT EXISTS source_activation_attestations (
    id uuid PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES community_sources(id) ON DELETE RESTRICT,
    activation_nonce uuid NOT NULL,
    attestation_generation integer NOT NULL CHECK (attestation_generation > 0),
    attestation_sha256 char(64) NOT NULL CHECK (attestation_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_attestation bytea NOT NULL,
    reviewed_sha char(40) NOT NULL CHECK (reviewed_sha ~ '^[0-9a-f]{40}$'),
    predecessor_attestation_sha256 char(64),
    authorization_evidence_sha256 char(64) NOT NULL,
    free_tier_evidence_sha256 char(64) NOT NULL,
    provenance_sha256 char(64) NOT NULL,
    evidence_database_time timestamptz NOT NULL,
    prepared_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
    created_at_db timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT uq_source_activation_attestation_identity
        UNIQUE (activation_nonce, attestation_generation, attestation_sha256),
    CONSTRAINT uq_source_activation_attestation_generation
        UNIQUE (activation_nonce, attestation_generation),
    CONSTRAINT uq_source_activation_attestation_binding
        UNIQUE (id, source_id, activation_nonce)
);
CREATE TABLE IF NOT EXISTS source_binding_change_intents (
    id uuid PRIMARY KEY,
    activation_nonce uuid NOT NULL UNIQUE,
    source_id uuid NOT NULL REFERENCES community_sources(id) ON DELETE RESTRICT,
    attestation_id uuid NOT NULL
        REFERENCES source_activation_attestations(id) ON DELETE RESTRICT,
    payload_sha256 char(64) NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    prestate_sha256 char(64) NOT NULL CHECK (prestate_sha256 ~ '^[0-9a-f]{64}$'),
    scope_version varchar(80) NOT NULL,
    created_at_db timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TABLE IF NOT EXISTS source_cadence_epochs (
    id uuid PRIMARY KEY,
    activation_nonce uuid NOT NULL,
    source_id uuid NOT NULL REFERENCES community_sources(id) ON DELETE RESTRICT,
    cadence_anchor_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    recheck_at timestamptz NOT NULL,
    closed_at timestamptz,
    created_at_db timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT source_cadence_window
        CHECK (expires_at = cadence_anchor_at + interval '31 days'),
    CONSTRAINT source_cadence_recheck_window
        CHECK (recheck_at > cadence_anchor_at AND recheck_at <= expires_at),
    CONSTRAINT source_cadence_close_window
        CHECK (closed_at IS NULL OR closed_at >= created_at_db),
    CONSTRAINT uq_source_cadence_epoch
        UNIQUE (source_id, activation_nonce, cadence_anchor_at),
    CONSTRAINT uq_source_cadence_binding UNIQUE (id, source_id, activation_nonce)
);
CREATE TABLE IF NOT EXISTS source_activation_state_transitions (
    id uuid PRIMARY KEY,
    activation_nonce uuid NOT NULL,
    source_id uuid NOT NULL REFERENCES community_sources(id) ON DELETE RESTRICT,
    attestation_id uuid
        REFERENCES source_activation_attestations(id) ON DELETE RESTRICT,
    binding_intent_id uuid
        REFERENCES source_binding_change_intents(id) ON DELETE RESTRICT,
    predecessor_transition_id uuid
        REFERENCES source_activation_state_transitions(id) ON DELETE RESTRICT,
    state varchar(32) NOT NULL CHECK (state IN (
        'prepared', 'binding_writing', 'binding_committed', 'handshake_passed',
        'anchor_reserved', 'github_finalized', 'active', 'deactivated',
        'restore_writing', 'restored', 'failed'
    )),
    current_authorization_id uuid
        REFERENCES source_authorization_decisions(id) ON DELETE RESTRICT,
    current_budget_id uuid REFERENCES provider_budget_records(id) ON DELETE RESTRICT,
    current_binding_id uuid
        REFERENCES source_binding_change_intents(id) ON DELETE RESTRICT,
    current_cadence_id uuid REFERENCES source_cadence_epochs(id) ON DELETE RESTRICT,
    receipt_sha256 char(64) NOT NULL CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    transition_at_db timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT uq_source_activation_transition_receipt
        UNIQUE (activation_nonce, receipt_sha256),
    CONSTRAINT source_active_transition_has_all_pointers CHECK (
        state <> 'active' OR (
            current_authorization_id IS NOT NULL
            AND current_budget_id IS NOT NULL
            AND current_binding_id IS NOT NULL
            AND current_cadence_id IS NOT NULL
        )
    )
);
ALTER TABLE community_sources ADD COLUMN IF NOT EXISTS current_budget_id uuid;
ALTER TABLE community_sources ADD COLUMN IF NOT EXISTS current_binding_id uuid;
ALTER TABLE community_sources ADD COLUMN IF NOT EXISTS current_cadence_id uuid;
ALTER TABLE community_sources DROP CONSTRAINT IF EXISTS enabled_requires_authorization;
ALTER TABLE community_sources
DROP CONSTRAINT IF EXISTS enabled_requires_activation_pointers;
ALTER TABLE community_sources
ADD CONSTRAINT enabled_requires_activation_pointers CHECK (
    platform <> 'manifold' OR NOT enabled OR (
        active_authorization_id IS NOT NULL
        AND current_budget_id IS NOT NULL
        AND current_binding_id IS NOT NULL
        AND current_cadence_id IS NOT NULL
    )
);
"""

FOREIGN_KEYS_SQL: Final = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_sources_current_budget'
    ) THEN
        ALTER TABLE community_sources ADD CONSTRAINT fk_sources_current_budget
        FOREIGN KEY (current_budget_id)
        REFERENCES provider_budget_records(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_sources_current_binding'
    ) THEN
        ALTER TABLE community_sources ADD CONSTRAINT fk_sources_current_binding
        FOREIGN KEY (current_binding_id)
        REFERENCES source_binding_change_intents(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_sources_current_cadence'
    ) THEN
        ALTER TABLE community_sources ADD CONSTRAINT fk_sources_current_cadence
        FOREIGN KEY (current_cadence_id)
        REFERENCES source_cadence_epochs(id) ON DELETE RESTRICT;
    END IF;
END
$$
"""

__all__ = ("FOREIGN_KEYS_SQL", "SCHEMA_SQL")
