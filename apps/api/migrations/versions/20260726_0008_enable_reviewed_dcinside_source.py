"""Enable the reviewed DCInside source with an audited free-tier budget."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260726_0008"
down_revision: str | None = "20260725_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist the exact reviewed source, scope, and no-spend budget."""
    op.execute(
        """
        INSERT INTO community_sources (
            id,
            country,
            platform,
            external_key,
            display_name,
            scope_version,
            enabled,
            active_authorization_id,
            created_at
        ) VALUES (
            gen_random_uuid(),
            'kr',
            'dcinside',
            'predictionmarket',
            'DCInside 예측마켓 미니 갤러리',
            'phase1-reviewed-v1',
            false,
            NULL,
            '2026-07-26T10:39:26Z'
        )
        """
    )
    op.execute(
        """
        INSERT INTO source_authorization_decisions (
            id,
            source_id,
            status,
            evidence_sha256,
            evidence_location,
            issuer,
            reviewer,
            permitted_scope,
            effective_at,
            expires_at,
            revoked_at,
            decided_at
        )
        SELECT
            gen_random_uuid(),
            id,
            'approved',
            '5b65d6694981b034c06aa7a2109903aa65a94bd130028613ab6f2b75db2532e2',
            'docs/evidence/source-scope-register.md',
            'repository-owner',
            'repository-owner',
            jsonb_build_object(
                'permitted_methods',
                jsonb_build_array('GET'),
                'permitted_routes',
                jsonb_build_array(
                    '/mini/board/lists/?id=predictionmarket',
                    '/mini/board/view/?id=predictionmarket&no={post_id}'
                ),
                'permitted_fields',
                jsonb_build_array(
                    'source_post_id',
                    'canonical_url',
                    'title',
                    'body',
                    'published_at',
                    'comments_count',
                    'upvote_or_score'
                ),
                'permitted_subreddits',
                jsonb_build_array(),
                'purpose',
                'personal_noncommercial_'
                    || 'prediction_market_monitoring_no_model_training',
                'requests_per_minute',
                30,
                'concurrency',
                1
            ),
            '2026-07-26T10:39:26Z',
            '2026-08-25T10:39:26Z',
            NULL,
            '2026-07-26T10:39:26Z'
        FROM community_sources
        WHERE
            platform = 'dcinside'
            AND external_key = 'predictionmarket'
            AND scope_version = 'phase1-reviewed-v1'
        """
    )
    op.execute(
        """
        UPDATE community_sources
        SET
            enabled = true,
            active_authorization_id = (
                SELECT id
                FROM source_authorization_decisions
                WHERE
                    source_id = community_sources.id
                    AND evidence_sha256 =
                        '5b65d6694981b034c06aa7a2109903aa65a94bd130028613ab6f2b75db2532e2'
                    AND effective_at = '2026-07-26T10:39:26Z'
            )
        WHERE
            platform = 'dcinside'
            AND external_key = 'predictionmarket'
            AND scope_version = 'phase1-reviewed-v1'
        """
    )
    op.execute(
        """
        INSERT INTO provider_budget_records (
            id,
            provider,
            billing_period_start,
            billing_period_end,
            observed_units,
            soft_stop_units,
            hard_stop_units,
            paid_spend_enabled,
            evidence_sha256,
            evidence_location,
            verified_at
        ) VALUES (
            gen_random_uuid(),
            'dcinside',
            '2026-07-26T10:39:26Z',
            '2026-08-25T10:39:26Z',
            0,
            70,
            80,
            false,
            'febbe6858fe6411bc43fe21609a75441dc4a622601f64ce356fcf70455b07c25',
            'docs/free-tier-operations.md#free-tier-70-80-v1',
            '2026-07-26T10:39:26Z'
        )
        """
    )


def downgrade() -> None:
    """Disable collection while preserving append-only audit evidence."""
    op.execute(
        """
        UPDATE community_sources
        SET
            enabled = false,
            active_authorization_id = NULL
        WHERE
            platform = 'dcinside'
            AND external_key = 'predictionmarket'
            AND scope_version = 'phase1-reviewed-v1'
        """
    )
