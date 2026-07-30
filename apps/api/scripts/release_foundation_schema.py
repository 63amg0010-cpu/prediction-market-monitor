"""Reversible 0011 DDL for generic post-bootstrap release receipts."""

UPGRADE_SQL = """
CREATE TABLE release_receipt_chain (
    receipt_sha256 char(64) PRIMARY KEY
        CONSTRAINT release_receipt_chain_sha
        CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
    canonical_receipt bytea NOT NULL,
    command varchar(100) NOT NULL,
    reviewed_sha char(40) NOT NULL,
    approved_plan_sha256 char(64) NOT NULL,
    approval_round_id char(64) NOT NULL,
    approval_launch_sha256s jsonb NOT NULL
        CHECK (
            jsonb_typeof(approval_launch_sha256s) = 'array'
            AND jsonb_array_length(approval_launch_sha256s) = 2
        ),
    activation_nonce uuid NOT NULL,
    dispatch_nonce uuid,
    attempt integer NOT NULL
        CONSTRAINT release_receipt_chain_attempt CHECK (attempt >= 0),
    accepted boolean NOT NULL,
    terminal_for_attempt boolean NOT NULL,
    retry_permitted boolean NOT NULL,
    predecessor_receipt_sha256 char(64),
    created_at_db timestamptz NOT NULL DEFAULT transaction_timestamp(),
    CONSTRAINT release_receipt_chain_accept_retry
        CHECK (NOT accepted OR NOT retry_permitted),
    CONSTRAINT release_receipt_chain_retry_terminal
        CHECK (NOT retry_permitted OR terminal_for_attempt)
);
INSERT INTO release_receipt_chain
SELECT
    receipt_sha256, canonical_receipt, 'review-root', reviewed_sha,
    approved_plan_sha256, approval_round_id, approval_launch_sha256s,
    activation_nonce, NULL, 0, true, true, false, NULL, created_at_db
FROM release_roots;
INSERT INTO release_receipt_chain
SELECT
    item.receipt_sha256, item.canonical_receipt, 'no-spend-preflight',
    item.reviewed_sha, item.approved_plan_sha256, root.approval_round_id,
    root.approval_launch_sha256s, item.activation_nonce, NULL, 0,
    item.accepted, true, false, item.predecessor_receipt_sha256,
    item.created_at_db
FROM release_no_spend_receipts AS item
JOIN release_roots AS root USING (activation_nonce);
INSERT INTO release_receipt_chain
SELECT
    item.receipt_sha256, item.canonical_receipt, 'migration-reservation',
    item.reviewed_sha, item.approved_plan_sha256, root.approval_round_id,
    root.approval_launch_sha256s, item.activation_nonce, item.dispatch_nonce,
    item.attempt, true, false, false, item.predecessor_receipt_sha256,
    item.reserved_at_db
FROM release_operation_reservations AS item
JOIN release_roots AS root USING (activation_nonce);
INSERT INTO release_receipt_chain
SELECT
    item.receipt_sha256, item.canonical_receipt, 'migrate-0010-bootstrap',
    item.reviewed_sha, item.approved_plan_sha256, root.approval_round_id,
    root.approval_launch_sha256s, item.activation_nonce, item.dispatch_nonce,
    item.attempt, item.accepted, item.terminal_for_attempt,
    item.retry_permitted, item.predecessor_receipt_sha256, item.created_at_db
FROM release_operation_receipts AS item
JOIN release_roots AS root USING (activation_nonce);
ALTER TABLE release_receipt_chain
ADD CONSTRAINT fk_release_receipt_chain_predecessor
FOREIGN KEY (predecessor_receipt_sha256)
REFERENCES release_receipt_chain(receipt_sha256) ON DELETE RESTRICT;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT fk_release_reservation_predecessor;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT release_reservation_operation;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT release_reservation_revision;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT release_reservation_attempt;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT release_reservation_claim_pair;
ALTER TABLE release_operation_receipts
DROP CONSTRAINT fk_release_operation_reservation_binding;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT uq_release_reservation_binding;
DROP INDEX uq_release_reservation_claimed_run;
ALTER TABLE release_operation_reservations
ALTER COLUMN operation DROP NOT NULL;
ALTER TABLE release_operation_reservations
ALTER COLUMN revision DROP NOT NULL;
ALTER TABLE release_operation_reservations
ADD COLUMN repository varchar(200);
ALTER TABLE release_operation_reservations
ADD COLUMN git_ref varchar(200);
ALTER TABLE release_operation_reservations
ADD COLUMN head_sha char(40);
ALTER TABLE release_operation_reservations
ADD COLUMN event_name varchar(32);
ALTER TABLE release_operation_reservations
ADD COLUMN approval_round_id char(64);
ALTER TABLE release_operation_reservations
ADD COLUMN approval_launch_sha256s jsonb;
ALTER TABLE release_operation_reservations
ADD COLUMN claimed_run_attempt integer;
UPDATE release_operation_reservations AS item
SET
    repository = '63amg0010-cpu/prediction-market-monitor',
    git_ref = 'refs/heads/main',
    head_sha = item.reviewed_sha,
    event_name = 'workflow_dispatch',
    approval_round_id = root.approval_round_id,
    approval_launch_sha256s = root.approval_launch_sha256s,
    claimed_run_attempt = CASE
        WHEN item.claimed_run_id IS NULL THEN NULL
        ELSE item.attempt
    END
FROM release_roots AS root
WHERE root.activation_nonce = item.activation_nonce;
ALTER TABLE release_operation_reservations
ALTER COLUMN repository SET NOT NULL;
ALTER TABLE release_operation_reservations
ALTER COLUMN git_ref SET NOT NULL;
ALTER TABLE release_operation_reservations
ALTER COLUMN head_sha SET NOT NULL;
ALTER TABLE release_operation_reservations
ALTER COLUMN event_name SET NOT NULL;
ALTER TABLE release_operation_reservations
ALTER COLUMN approval_round_id SET NOT NULL;
ALTER TABLE release_operation_reservations
ALTER COLUMN approval_launch_sha256s SET NOT NULL;
ALTER TABLE release_operation_reservations
ADD CONSTRAINT release_reservation_attempt_positive CHECK (attempt >= 1);
ALTER TABLE release_operation_reservations
ADD CONSTRAINT release_reservation_claim_pair CHECK (
    (claimed_run_id IS NULL) = (claimed_run_attempt IS NULL)
    AND (claimed_run_id IS NULL) = (claimed_at_db IS NULL)
);
ALTER TABLE release_operation_reservations
ADD CONSTRAINT fk_release_reservation_predecessor
FOREIGN KEY (predecessor_receipt_sha256)
REFERENCES release_receipt_chain(receipt_sha256) ON DELETE RESTRICT;
ALTER TABLE release_operation_reservations
ADD CONSTRAINT uq_release_reservation_binding UNIQUE (
    receipt_sha256, reviewed_sha, approved_plan_sha256,
    activation_nonce, dispatch_nonce, attempt
);
CREATE UNIQUE INDEX uq_release_reservation_claimed_run
ON release_operation_reservations (repository, claimed_run_id)
WHERE claimed_run_id IS NOT NULL;
ALTER TABLE release_operation_receipts
DROP CONSTRAINT uq_release_operation_identity;
ALTER TABLE release_operation_receipts
DROP CONSTRAINT release_operation_receipts_operation_check;
ALTER TABLE release_operation_receipts
DROP CONSTRAINT release_operation_receipts_revision_check;
ALTER TABLE release_operation_receipts
DROP CONSTRAINT release_operation_receipts_attempt_check;
ALTER TABLE release_operation_receipts
ALTER COLUMN operation DROP NOT NULL;
ALTER TABLE release_operation_receipts
ALTER COLUMN revision DROP NOT NULL;
ALTER TABLE release_operation_receipts
ADD CONSTRAINT release_operation_attempt_positive CHECK (attempt >= 1);
ALTER TABLE release_operation_receipts
ADD CONSTRAINT fk_release_operation_reservation_binding FOREIGN KEY (
    reservation_receipt_sha256, reviewed_sha, approved_plan_sha256,
    activation_nonce, dispatch_nonce, attempt
) REFERENCES release_operation_reservations (
    receipt_sha256, reviewed_sha, approved_plan_sha256,
    activation_nonce, dispatch_nonce, attempt
) ON DELETE RESTRICT;
ALTER TABLE release_operation_receipts
ADD CONSTRAINT uq_release_operation_identity UNIQUE (
    activation_nonce, dispatch_nonce, attempt, run_id
);
ALTER TABLE public.release_receipt_chain ENABLE ROW LEVEL SECURITY;
"""

DOWNGRADE_GUARD_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM release_receipt_chain
        WHERE command NOT IN (
            'review-root', 'no-spend-preflight',
            'migration-reservation', 'migrate-0010-bootstrap'
        )
    ) THEN
        RAISE EXCEPTION 'release_foundation_downgrade_dependency'
            USING ERRCODE = '55000';
    END IF;
END
$$
"""

DOWNGRADE_SQL = """
DROP INDEX uq_release_reservation_claimed_run;
ALTER TABLE release_operation_receipts
DROP CONSTRAINT fk_release_operation_reservation_binding;
ALTER TABLE release_operation_receipts
DROP CONSTRAINT uq_release_operation_identity;
ALTER TABLE release_operation_receipts
DROP CONSTRAINT release_operation_attempt_positive;
ALTER TABLE release_operation_receipts
ALTER COLUMN operation SET NOT NULL;
ALTER TABLE release_operation_receipts
ALTER COLUMN revision SET NOT NULL;
ALTER TABLE release_operation_receipts
ADD CHECK (operation IN ('upgrade', 'downgrade'));
ALTER TABLE release_operation_receipts
ADD CHECK (revision IN ('20260727_0010', '20260727_0011'));
ALTER TABLE release_operation_receipts
ADD CHECK (attempt IN (1, 2));
ALTER TABLE release_operation_reservations
DROP CONSTRAINT fk_release_reservation_predecessor;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT release_reservation_attempt_positive;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT release_reservation_claim_pair;
ALTER TABLE release_operation_reservations
DROP CONSTRAINT uq_release_reservation_binding;
ALTER TABLE release_operation_reservations
DROP COLUMN claimed_run_attempt;
ALTER TABLE release_operation_reservations
DROP COLUMN approval_launch_sha256s;
ALTER TABLE release_operation_reservations
DROP COLUMN approval_round_id;
ALTER TABLE release_operation_reservations
DROP COLUMN head_sha;
ALTER TABLE release_operation_reservations
DROP COLUMN event_name;
ALTER TABLE release_operation_reservations
DROP COLUMN git_ref;
ALTER TABLE release_operation_reservations
DROP COLUMN repository;
ALTER TABLE release_operation_reservations
ALTER COLUMN operation SET NOT NULL;
ALTER TABLE release_operation_reservations
ALTER COLUMN revision SET NOT NULL;
ALTER TABLE release_operation_reservations
ADD CONSTRAINT release_reservation_operation
CHECK (operation IN ('upgrade', 'downgrade'));
ALTER TABLE release_operation_reservations
ADD CONSTRAINT release_reservation_revision
CHECK (revision IN ('20260727_0010', '20260727_0011'));
ALTER TABLE release_operation_reservations
ADD CONSTRAINT release_reservation_attempt CHECK (attempt IN (1, 2));
ALTER TABLE release_operation_reservations
ADD CONSTRAINT release_reservation_claim_pair
CHECK ((claimed_run_id IS NULL) = (claimed_at_db IS NULL));
ALTER TABLE release_operation_reservations
ADD CONSTRAINT fk_release_reservation_predecessor FOREIGN KEY (
    predecessor_receipt_sha256, reviewed_sha,
    approved_plan_sha256, activation_nonce
) REFERENCES release_no_spend_receipts (
    receipt_sha256, reviewed_sha, approved_plan_sha256, activation_nonce
) ON DELETE RESTRICT;
ALTER TABLE release_operation_reservations
ADD CONSTRAINT uq_release_reservation_binding UNIQUE (
    receipt_sha256, reviewed_sha, approved_plan_sha256, activation_nonce,
    dispatch_nonce, operation, revision, attempt
);
ALTER TABLE release_operation_receipts
ADD CONSTRAINT fk_release_operation_reservation_binding FOREIGN KEY (
    reservation_receipt_sha256, reviewed_sha, approved_plan_sha256,
    activation_nonce, dispatch_nonce, operation, revision, attempt
) REFERENCES release_operation_reservations (
    receipt_sha256, reviewed_sha, approved_plan_sha256,
    activation_nonce, dispatch_nonce, operation, revision, attempt
) ON DELETE RESTRICT;
ALTER TABLE release_operation_receipts
ADD CONSTRAINT uq_release_operation_identity UNIQUE (
    activation_nonce, dispatch_nonce, operation, revision, attempt, run_id
);
CREATE UNIQUE INDEX uq_release_reservation_claimed_run
ON release_operation_reservations (claimed_run_id) NULLS NOT DISTINCT;
DROP TABLE release_receipt_chain
"""

__all__ = ("DOWNGRADE_GUARD_SQL", "DOWNGRADE_SQL", "UPGRADE_SQL")
