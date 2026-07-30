"""SQL for atomic cadence workflow recording."""

from sqlalchemy import text

LOCK = text(
    "SELECT pg_advisory_xact_lock(hashtext(CAST(CAST(:epoch_id AS uuid) AS text)))"
)
LOAD = text(
    """
    SELECT c.epoch_sha256, c.dcinside_source_id, c.manifold_source_id,
           c.binding_sha256, c.scope_sha256, c.invalidated_at,
           s.schedule_kind, s.slot_key, s.due_at, s.accepted_attempt_id,
           transaction_timestamp() AS db_now
    FROM cadence_epoch_contracts c
    JOIN cadence_workflow_slots s
      ON s.cadence_epoch_id = c.cadence_epoch_id
    WHERE c.cadence_epoch_id = :epoch_id
      AND s.schedule_kind = :schedule_kind AND s.slot_key = :slot_key
    FOR UPDATE OF c, s
    """
)
LOAD_FAILED = text(
    """
    SELECT accepted, retry_permitted, cadence_epoch_id, schedule_kind, slot_key
    FROM cadence_workflow_attempts
    WHERE attempt_id = :attempt_id
    FOR UPDATE
    """
)
LOAD_LOGICAL_ATTEMPT = text(
    """
    SELECT attempt_id
    FROM cadence_workflow_attempts
    WHERE cadence_epoch_id=:epoch_id AND schedule_kind=:schedule_kind
      AND slot_key=:slot_key AND cadence_attempt=:cadence_attempt
    FOR UPDATE
    """
)
INSERT_ATTEMPT = text(
    """
    INSERT INTO cadence_workflow_attempts (
      attempt_id, cadence_epoch_id, schedule_kind, slot_key, workflow_mode,
      workflow_file, workflow_run_id, workflow_run_attempt, cadence_attempt,
      failed_predecessor_attempt_id, started_at, completed_at, epoch_sha256,
      binding_sha256, scope_sha256, eligible, accepted, reason_code,
      retry_permitted
    ) VALUES (
      :attempt_id, :epoch_id, :schedule_kind, :slot_key, :workflow_mode,
      :workflow_file, :workflow_run_id, :workflow_run_attempt, :cadence_attempt,
      :failed_predecessor_attempt_id, :started_at, :completed_at, :epoch_sha256,
      :binding_sha256, :scope_sha256, :eligible, false, :reason_code,
      :retry_permitted
    ) ON CONFLICT (attempt_id) DO NOTHING
    """
)
INSERT_SOURCE = text(
    """
    INSERT INTO cadence_attempt_source_receipts (
      attempt_id, source_id, succeeded, receipt_sha256
    ) VALUES (:attempt_id, :source_id, :succeeded, :receipt_sha256)
    ON CONFLICT (attempt_id, source_id) DO NOTHING
    """
)
CAS = text(
    """
    UPDATE cadence_workflow_slots SET accepted_attempt_id = :attempt_id
    WHERE cadence_epoch_id = :epoch_id AND schedule_kind = :schedule_kind
      AND slot_key = :slot_key AND accepted_attempt_id IS NULL
    """
)
FINALIZE = text(
    """
    UPDATE cadence_workflow_attempts
    SET accepted=:accepted, reason_code=:reason_code,
        retry_permitted=:retry_permitted
    WHERE attempt_id=:attempt_id
    """
)
EXISTING = text(
    """
    SELECT accepted, reason_code, retry_permitted, created_at_db,
           cadence_epoch_id, schedule_kind, slot_key, workflow_mode,
           workflow_file, workflow_run_id, workflow_run_attempt,
           cadence_attempt, failed_predecessor_attempt_id, started_at,
           completed_at
    FROM cadence_workflow_attempts WHERE attempt_id=:attempt_id
    """
)
EXISTING_SOURCES = text(
    """
    SELECT source_id, succeeded, receipt_sha256
    FROM cadence_attempt_source_receipts
    WHERE attempt_id=:attempt_id ORDER BY source_id
    """
)
