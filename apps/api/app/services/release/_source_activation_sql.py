"""SQL statements for serialized source activation writes."""

from datetime import datetime
from typing import Final

from sqlalchemy import DateTime, Select, TextClause, literal_column, select, text

ADVISORY_LOCK: Final[TextClause] = text(
    "SELECT pg_advisory_xact_lock(hashtext('production-collector-binding'))"
)
DATABASE_NOW: Final[Select[tuple[datetime]]] = select(
    literal_column("transaction_timestamp()", DateTime(timezone=True))
)
INSERT_CADENCE: Final[TextClause] = text(
    """
    INSERT INTO source_cadence_epochs (
        id, activation_nonce, source_id, cadence_anchor_at,
        expires_at, recheck_at, created_at_db
    ) VALUES (
        :cadence_id, :nonce, :source_id, :anchor,
        :expires, :recheck, :db_now
    ) ON CONFLICT (source_id, activation_nonce, cadence_anchor_at) DO NOTHING
    """
)
INSERT_AUTHORIZATION: Final[TextClause] = text(
    """
    INSERT INTO source_authorization_decisions (
        id, source_id, status, evidence_sha256, evidence_location,
        issuer, reviewer, permitted_scope, effective_at, expires_at, decided_at
    ) VALUES (
        :authorization_id, :source_id, 'approved', :authorization_sha,
        'release-gate:activation', 'Manifold Markets, Inc.',
        'repository-owner-approved-plan-2026-07-27',
        CAST(:scope AS jsonb), :effective, :expires, :db_now
    ) ON CONFLICT (source_id, evidence_sha256, effective_at) DO NOTHING
    """
)
INSERT_BUDGET: Final[TextClause] = text(
    """
    INSERT INTO provider_budget_records (
        id, provider, billing_period_start, billing_period_end,
        observed_units, soft_stop_units, hard_stop_units,
        paid_spend_enabled, evidence_sha256, evidence_location, verified_at
    ) VALUES (
        :budget_id, 'manifold', :effective, :expires, 0, 70, 80,
        false, :budget_sha, 'release-gate:no-spend', :db_now
    ) ON CONFLICT (provider, billing_period_start) DO UPDATE SET
        soft_stop_units = EXCLUDED.soft_stop_units,
        hard_stop_units = EXCLUDED.hard_stop_units
    WHERE provider_budget_records.observed_units = 0
      AND provider_budget_records.soft_stop_units IN (0, 70)
      AND provider_budget_records.hard_stop_units IN (0, 80)
      AND provider_budget_records.paid_spend_enabled = false
      AND provider_budget_records.evidence_sha256 = EXCLUDED.evidence_sha256
    """
)
ACTIVATE_SOURCE: Final[TextClause] = text(
    """
    UPDATE community_sources SET
        enabled = true, active_authorization_id = :authorization_id,
        current_budget_id = :budget_id, current_binding_id = :binding_id,
        current_cadence_id = :cadence_id
    WHERE id = :source_id AND (
      (enabled = false AND active_authorization_id IS NULL
       AND current_budget_id IS NULL AND current_binding_id IS NULL
       AND current_cadence_id IS NULL)
      OR (enabled = true AND active_authorization_id = :authorization_id
       AND current_budget_id = :budget_id AND current_binding_id = :binding_id
       AND current_cadence_id = :cadence_id)
    )
    """
)
INSERT_ATTESTATION: Final[TextClause] = text(
    """
    INSERT INTO source_activation_attestations (
        id, source_id, activation_nonce, attestation_generation,
        attestation_sha256, canonical_attestation, reviewed_sha,
        predecessor_attestation_sha256, authorization_evidence_sha256,
        free_tier_evidence_sha256, provenance_sha256,
        evidence_database_time, prepared_at, created_at_db
    ) SELECT
        :attestation_id, :source_id, :nonce, :generation,
        :attestation_sha, :attestation, :reviewed_sha,
        prior.attestation_sha256, :authorization_sha, :free_tier_sha,
        :provenance_sha, :evidence_time, :prepared_at, :db_now
    FROM source_activation_attestations prior
    WHERE prior.id = :prior_attestation_id
    ON CONFLICT (activation_nonce, attestation_generation) DO NOTHING
    """
)
ROTATE_INTENT: Final[TextClause] = text(
    """
    UPDATE source_binding_change_intents SET
        attestation_id = :attestation_id, payload_sha256 = :payload_sha
    WHERE id = :binding_id AND activation_nonce = :nonce
      AND attestation_id = :prior_attestation_id
    """
)
DISABLE_SOURCE: Final[TextClause] = text(
    """
    UPDATE community_sources SET enabled = false,
        active_authorization_id = NULL, current_budget_id = NULL,
        current_binding_id = NULL, current_cadence_id = NULL
    WHERE id = :source_id
    """
)
INSERT_DEACTIVATED: Final[TextClause] = text(
    """
    INSERT INTO source_activation_state_transitions (
        id, activation_nonce, source_id, attestation_id,
        binding_intent_id, predecessor_transition_id, state,
        receipt_sha256, transition_at_db
    ) SELECT
        :deactivated_id, :nonce, :source_id, :prior_attestation_id,
        :binding_id, :predecessor_id, 'deactivated',
        :deactivated_receipt, :db_now
    FROM source_activation_state_transitions prior
    WHERE prior.id = :predecessor_id AND NOT EXISTS (
        SELECT 1 FROM source_activation_state_transitions newer
        WHERE newer.predecessor_transition_id = prior.id
    )
    ON CONFLICT (activation_nonce, receipt_sha256) DO NOTHING
    """
)
INSERT_TRANSITION: Final[TextClause] = text(
    """
    INSERT INTO source_activation_state_transitions (
        id, activation_nonce, source_id, attestation_id,
        binding_intent_id, predecessor_transition_id, state,
        current_authorization_id, current_budget_id,
        current_binding_id, current_cadence_id,
        receipt_sha256, transition_at_db
    ) SELECT
        :transition_id, :nonce, :source_id, :attestation_id,
        :binding_id, :predecessor_id, :state,
        :authorization_id, :budget_id, :current_binding_id,
        :cadence_id, :receipt, :db_now
    FROM source_activation_state_transitions prior
    WHERE prior.id = :predecessor_id AND NOT EXISTS (
        SELECT 1 FROM source_activation_state_transitions newer
        WHERE newer.predecessor_transition_id = prior.id
    )
    ON CONFLICT (activation_nonce, receipt_sha256) DO NOTHING
    """
)
