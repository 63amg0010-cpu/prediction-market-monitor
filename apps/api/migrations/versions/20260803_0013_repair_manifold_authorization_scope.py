"""Append the exact reviewed Manifold authorization scope."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260803_0013"
down_revision: str | None = "20260803_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve the old decision and append its exact reviewed successor."""
    op.execute(
        """
        DO $$
        DECLARE
            source_row record;
            old_authorization record;
            repaired_authorization record;
            latest_transition record;
            expected_old_scope jsonb;
            expected_new_scope jsonb;
            changed integer;
        BEGIN
            expected_old_scope := jsonb_build_object(
                'concurrency', 1,
                'permitted_fields', jsonb_build_array(
                    'source_post_id', 'canonical_url', 'title', 'body',
                    'published_at', 'comments_count', 'upvote_or_score'
                ),
                'permitted_methods', jsonb_build_array('GET'),
                'permitted_routes', jsonb_build_array(
                    '/v0/comments', '/v0/markets'
                ),
                'permitted_subreddits', jsonb_build_array(),
                'purpose',
                    'personal_noncommercial_prediction_market_monitoring_no_model_training',
                'requests_per_minute', 30
            );
            expected_new_scope := jsonb_build_object(
                'concurrency', 1,
                'permitted_fields', jsonb_build_array(
                    'market.id', 'market.question', 'market.market_slug',
                    'market.neutral_url', 'comment.id', 'comment.contractId',
                    'comment.createdTime', 'comment.content.text'
                ),
                'permitted_methods', jsonb_build_array('GET'),
                'permitted_routes', jsonb_build_array(
                    '/v0/comments', '/v0/markets'
                ),
                'permitted_subreddits', jsonb_build_array(),
                'purpose',
                    'personal_noncommercial_prediction_market_monitoring_no_model_training',
                'requests_per_minute', 30
            );

            SELECT id, enabled, active_authorization_id
            INTO source_row
            FROM community_sources
            WHERE id = '0890756a-ca23-5697-ae4c-0de527361064'
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'manifold source is missing';
            END IF;
            IF source_row.active_authorization_id IS NULL THEN
                RETURN;
            END IF;

            SELECT * INTO old_authorization
            FROM source_authorization_decisions
            WHERE id = source_row.active_authorization_id;
            IF old_authorization.id = 'e389531c-c167-4a0a-a839-acc828cd7364'
               AND old_authorization.permitted_scope = expected_new_scope THEN
                RETURN;
            END IF;
            IF source_row.enabled <> true
               OR old_authorization.status <> 'approved'
               OR old_authorization.revoked_at IS NOT NULL
               OR old_authorization.permitted_scope <> expected_old_scope THEN
                RAISE EXCEPTION 'manifold authorization repair precondition failed';
            END IF;

            INSERT INTO source_authorization_decisions (
                id, source_id, status, evidence_sha256, evidence_location,
                issuer, reviewer, permitted_scope, effective_at,
                expires_at, revoked_at, decided_at
            ) VALUES (
                'e389531c-c167-4a0a-a839-acc828cd7364',
                old_authorization.source_id,
                old_authorization.status,
                old_authorization.evidence_sha256,
                old_authorization.evidence_location,
                old_authorization.issuer,
                old_authorization.reviewer,
                expected_new_scope,
                old_authorization.effective_at + interval '1 microsecond',
                old_authorization.expires_at,
                NULL,
                transaction_timestamp()
            ) ON CONFLICT DO NOTHING;

            SELECT * INTO repaired_authorization
            FROM source_authorization_decisions
            WHERE id = 'e389531c-c167-4a0a-a839-acc828cd7364';
            IF repaired_authorization.source_id <> source_row.id
               OR repaired_authorization.status <> 'approved'
               OR repaired_authorization.permitted_scope <> expected_new_scope
               OR repaired_authorization.revoked_at IS NOT NULL THEN
                RAISE EXCEPTION 'repaired manifold authorization verification failed';
            END IF;

            SELECT * INTO latest_transition
            FROM source_activation_state_transitions
            WHERE source_id = source_row.id
            ORDER BY transition_at_db DESC, id DESC
            LIMIT 1
            FOR UPDATE;
            IF latest_transition.state <> 'active'
               OR latest_transition.current_authorization_id
                    <> old_authorization.id THEN
                RAISE EXCEPTION 'active transition authorization precondition failed';
            END IF;

            UPDATE community_sources
            SET active_authorization_id = repaired_authorization.id
            WHERE id = source_row.id
              AND active_authorization_id = old_authorization.id;
            GET DIAGNOSTICS changed = ROW_COUNT;
            IF changed <> 1 THEN
                RAISE EXCEPTION 'manifold authorization pointer repair raced';
            END IF;

            INSERT INTO source_activation_state_transitions (
                id, activation_nonce, source_id, attestation_id,
                binding_intent_id, predecessor_transition_id, state,
                current_authorization_id, current_budget_id,
                current_binding_id, current_cadence_id,
                receipt_sha256, transition_at_db
            ) VALUES (
                '95b17982-28b6-43d1-889c-8a3461384097',
                latest_transition.activation_nonce,
                latest_transition.source_id,
                latest_transition.attestation_id,
                latest_transition.binding_intent_id,
                latest_transition.id,
                'active',
                repaired_authorization.id,
                latest_transition.current_budget_id,
                latest_transition.current_binding_id,
                latest_transition.current_cadence_id,
                'ea2c86b88487f0c48e6e3eaf09f0d5213e1f47e2989ba9b9e4bf1356cfe81176',
                transaction_timestamp()
            ) ON CONFLICT (activation_nonce, receipt_sha256) DO NOTHING;
        END $$
        """
    )


def downgrade() -> None:
    """Retain the reviewed append-only correction and its active pointer."""
    op.execute("SELECT 1")
