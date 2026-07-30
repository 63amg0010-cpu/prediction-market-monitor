"""SQL statements for cadence materialization and attempt CAS."""

from typing import Final

from sqlalchemy import TextClause, text

UUID_CANONICAL_TEXT: Final = "CAST(CAST(:epoch_id AS uuid) AS text)"
LOCK_EPOCH: Final[TextClause] = text(
    f"SELECT pg_advisory_xact_lock(hashtext({UUID_CANONICAL_TEXT}))"
)
CURRENT_EPOCH: Final[TextClause] = text(
    """
    SELECT epoch.cadence_anchor_at, epoch.recheck_at, epoch.closed_at,
           source.enabled, source.current_cadence_id,
           transition.state, transition.current_cadence_id AS transition_cadence_id,
           binding.payload_sha256 AS current_binding_sha256
    FROM source_cadence_epochs AS epoch
    JOIN community_sources AS source ON source.id = epoch.source_id
    JOIN source_activation_state_transitions AS transition
      ON transition.activation_nonce = epoch.activation_nonce
     AND transition.source_id = epoch.source_id
    LEFT JOIN source_activation_state_transitions AS newer
      ON newer.predecessor_transition_id = transition.id
    LEFT JOIN source_binding_change_intents AS binding
      ON binding.id = transition.current_binding_id
    WHERE epoch.id = :epoch_id AND newer.id IS NULL
    FOR UPDATE OF epoch, source, transition
    """
)
EXPECTED_SOURCES: Final[TextClause] = text(
    """
    SELECT id, platform::text AS platform, enabled
    FROM community_sources
    WHERE id IN (:source_a, :source_b)
    ORDER BY platform::text
    FOR SHARE
    """
)
INSERT_CONTRACT: Final[TextClause] = text(
    """
    INSERT INTO cadence_epoch_contracts (
        cadence_epoch_id, epoch_sha256, dcinside_source_id,
        manifold_source_id, binding_sha256, scope_sha256,
        window_closes_at
    ) VALUES (
        :epoch_id, :epoch_sha, :dcinside_id, :manifold_id,
        :binding_sha, :scope_sha, :closes_at
    ) ON CONFLICT (cadence_epoch_id) DO NOTHING
    """
)
SELECT_CONTRACT: Final[TextClause] = text(
    """
    SELECT epoch_sha256, dcinside_source_id, manifold_source_id,
           binding_sha256, scope_sha256, window_closes_at, invalidated_at
    FROM cadence_epoch_contracts
    WHERE cadence_epoch_id = :epoch_id
    FOR UPDATE
    """
)
INSERT_SLOT: Final[TextClause] = text(
    """
    INSERT INTO cadence_workflow_slots (
        cadence_epoch_id, schedule_kind, slot_key, due_at
    ) VALUES (
        :epoch_id, :schedule_kind, :slot_key, :due_at
    ) ON CONFLICT (cadence_epoch_id, schedule_kind, slot_key) DO NOTHING
    """
)
COUNT_SLOTS: Final[TextClause] = text(
    """
    SELECT schedule_kind, count(*) AS slot_count
    FROM cadence_workflow_slots
    WHERE cadence_epoch_id = :epoch_id
    GROUP BY schedule_kind
    """
)
SELECT_SLOT: Final[TextClause] = text(
    """
    SELECT schedule_kind, slot_key, due_at, accepted_attempt_id
    FROM cadence_workflow_slots
    WHERE cadence_epoch_id = :epoch_id
      AND schedule_kind = :schedule_kind AND slot_key = :slot_key
    FOR UPDATE
    """
)
INSERT_ATTEMPT: Final[TextClause] = text(
    """
    INSERT INTO cadence_workflow_attempts (
        attempt_id, cadence_epoch_id, schedule_kind, slot_key,
        workflow_mode, started_at, completed_at, epoch_sha256,
        binding_sha256, scope_sha256, workflow_file, workflow_run_id,
        workflow_run_attempt, cadence_attempt,
        failed_predecessor_attempt_id, eligible, accepted,
        reason_code, retry_permitted
    ) VALUES (
        :attempt_id, :epoch_id, :schedule_kind, :slot_key,
        :workflow_mode, :started_at, :completed_at, :epoch_sha,
        :binding_sha, :scope_sha, :workflow_file, :workflow_run_id,
        :workflow_run_attempt, :cadence_attempt,
        :failed_predecessor_attempt_id, :eligible, false,
        :reason, :retry_permitted
    ) ON CONFLICT (attempt_id) DO NOTHING
    """
)
INSERT_SUBRECEIPT: Final[TextClause] = text(
    """
    INSERT INTO cadence_attempt_source_receipts (
        attempt_id, source_id, succeeded, receipt_sha256
    ) VALUES (
        :attempt_id, :source_id, :succeeded, :receipt_sha
    ) ON CONFLICT (attempt_id, source_id) DO NOTHING
    """
)
CAS_SLOT: Final[TextClause] = text(
    """
    UPDATE cadence_workflow_slots
    SET accepted_attempt_id = :attempt_id
    WHERE cadence_epoch_id = :epoch_id
      AND schedule_kind = :schedule_kind AND slot_key = :slot_key
      AND accepted_attempt_id IS NULL
    """
)
FINALIZE_ATTEMPT: Final[TextClause] = text(
    """
    UPDATE cadence_workflow_attempts
    SET accepted = :accepted, reason_code = :reason,
        retry_permitted = :retry_permitted
    WHERE attempt_id = :attempt_id
    """
)

__all__ = (
    "CAS_SLOT",
    "COUNT_SLOTS",
    "CURRENT_EPOCH",
    "EXPECTED_SOURCES",
    "FINALIZE_ATTEMPT",
    "INSERT_ATTEMPT",
    "INSERT_CONTRACT",
    "INSERT_SLOT",
    "INSERT_SUBRECEIPT",
    "LOCK_EPOCH",
    "SELECT_CONTRACT",
    "SELECT_SLOT",
)
