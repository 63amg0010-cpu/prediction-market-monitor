"""Repair the active multi-source scope and Manifold free-budget policy."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_0012"
down_revision: str | None = "20260727_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Align both reviewed sources and repair only the exact zeroed budget."""
    op.execute(
        """
        UPDATE community_sources
        SET scope_version = 'phase1-reviewed-v1+manifold-v1'
        WHERE id IN (
            'd6dc5ea1-e3af-4bfe-88ad-e4beffd22ab6',
            '0890756a-ca23-5697-ae4c-0de527361064'
        )
          AND scope_version IN (
              'phase1-reviewed-v1',
              'phase1-reviewed-v1+manifold-v1'
          )
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            source_row record;
            old_budget record;
            repaired_budget record;
            latest_transition record;
            changed integer;
        BEGIN
            SELECT id, current_budget_id
            INTO source_row
            FROM community_sources
            WHERE id = '0890756a-ca23-5697-ae4c-0de527361064'
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'manifold source is missing';
            END IF;
            IF source_row.current_budget_id IS NULL THEN
                RETURN;
            END IF;

            SELECT * INTO old_budget
            FROM provider_budget_records
            WHERE id = source_row.current_budget_id;
            IF old_budget.soft_stop_units = 70
               AND old_budget.hard_stop_units = 80
               AND old_budget.paid_spend_enabled = false THEN
                RETURN;
            END IF;
            IF old_budget.provider <> 'manifold'
               OR old_budget.observed_units <> 0
               OR old_budget.soft_stop_units <> 0
               OR old_budget.hard_stop_units <> 0
               OR old_budget.paid_spend_enabled <> false
               OR old_budget.evidence_location <> 'release-gate:no-spend' THEN
                RAISE EXCEPTION 'manifold budget repair precondition failed';
            END IF;

            INSERT INTO provider_budget_records (
                id, provider, billing_period_start, billing_period_end,
                observed_units, soft_stop_units, hard_stop_units,
                paid_spend_enabled, evidence_sha256, evidence_location,
                verified_at
            ) VALUES (
                'b0b091ea-2b16-4442-bbe2-19e4936a3c0c',
                old_budget.provider,
                old_budget.billing_period_start + interval '1 microsecond',
                old_budget.billing_period_end,
                0, 70, 80, false, old_budget.evidence_sha256,
                'release-gate:no-spend#free-tier-70-80-v1-repair',
                transaction_timestamp()
            ) ON CONFLICT DO NOTHING;

            SELECT * INTO repaired_budget
            FROM provider_budget_records
            WHERE id = 'b0b091ea-2b16-4442-bbe2-19e4936a3c0c';
            IF repaired_budget.provider <> 'manifold'
               OR repaired_budget.observed_units <> 0
               OR repaired_budget.soft_stop_units <> 70
               OR repaired_budget.hard_stop_units <> 80
               OR repaired_budget.paid_spend_enabled <> false THEN
                RAISE EXCEPTION 'repaired manifold budget verification failed';
            END IF;

            SELECT * INTO latest_transition
            FROM source_activation_state_transitions
            WHERE source_id = source_row.id
            ORDER BY transition_at_db DESC, id DESC
            LIMIT 1
            FOR UPDATE;
            IF latest_transition.state <> 'active'
               OR latest_transition.current_budget_id <> old_budget.id THEN
                RAISE EXCEPTION 'active transition budget precondition failed';
            END IF;

            UPDATE community_sources
            SET current_budget_id = repaired_budget.id
            WHERE id = source_row.id
              AND current_budget_id = old_budget.id;
            GET DIAGNOSTICS changed = ROW_COUNT;
            IF changed <> 1 THEN
                RAISE EXCEPTION 'manifold budget pointer repair raced';
            END IF;

            INSERT INTO source_activation_state_transitions (
                id, activation_nonce, source_id, attestation_id,
                binding_intent_id, predecessor_transition_id, state,
                current_authorization_id, current_budget_id,
                current_binding_id, current_cadence_id,
                receipt_sha256, transition_at_db
            ) VALUES (
                '3278ba2d-2203-4a29-bd9d-f7dc7fb0107a',
                latest_transition.activation_nonce,
                latest_transition.source_id,
                latest_transition.attestation_id,
                latest_transition.binding_intent_id,
                latest_transition.id,
                'active',
                latest_transition.current_authorization_id,
                repaired_budget.id,
                latest_transition.current_binding_id,
                latest_transition.current_cadence_id,
                '73b161693cfb39821e6c87ca504dcc7471bf3c028cbafcdf4b34a6c602d02abd',
                transaction_timestamp()
            ) ON CONFLICT (activation_nonce, receipt_sha256) DO NOTHING;
        END $$
        """
    )


def downgrade() -> None:
    """Retain the safe data correction instead of restoring an invalid policy."""
    op.execute("SELECT 1")
