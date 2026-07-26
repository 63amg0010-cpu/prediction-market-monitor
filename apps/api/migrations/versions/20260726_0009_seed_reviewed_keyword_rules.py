"""Seed the immutable reviewed keyword vocabulary and existing post matches."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260726_0009"
down_revision: str | None = "20260726_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    """Persist reviewed rules and deterministic matches for collected revisions."""
    op.execute(
        """
        INSERT INTO keyword_rule_sets (
            id, version, rules_hash, reviewed_at
        ) VALUES (
            '90000000-0000-4000-8000-000000000001',
            '1.0.0',
            'e72c1ebdae3f7318a76dfee09408730ab52169cad1a2dbc65ac24d277eca1a8d',
            '2026-07-26T13:15:00Z'
        )
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO keyword_rules (
            id,
            rule_set_id,
            language,
            phrase,
            normalized_phrase,
            category,
            enabled
        ) VALUES
            ('91000000-0000-4000-8000-000000000001',
             '90000000-0000-4000-8000-000000000001',
             'en', 'prediction market', 'prediction market', 'market', true),
            ('91000000-0000-4000-8000-000000000002',
             '90000000-0000-4000-8000-000000000001',
             'ko', '예측시장', '예측시장', 'market', true),
            ('91000000-0000-4000-8000-000000000003',
             '90000000-0000-4000-8000-000000000001',
             'en', 'Polymarket', 'polymarket', 'platform', true),
            ('91000000-0000-4000-8000-000000000004',
             '90000000-0000-4000-8000-000000000001',
             'ko', '폴리마켓', '폴리마켓', 'platform', true),
            ('91000000-0000-4000-8000-000000000005',
             '90000000-0000-4000-8000-000000000001',
             'en', 'Kalshi', 'kalshi', 'platform', true),
            ('91000000-0000-4000-8000-000000000006',
             '90000000-0000-4000-8000-000000000001',
             'ko', '칼시', '칼시', 'platform', true),
            ('91000000-0000-4000-8000-000000000007',
             '90000000-0000-4000-8000-000000000001',
             'en', 'implied probability', 'implied probability', 'probability', true),
            ('91000000-0000-4000-8000-000000000008',
             '90000000-0000-4000-8000-000000000001',
             'ko', '확률', '확률', 'probability', true),
            ('91000000-0000-4000-8000-000000000009',
             '90000000-0000-4000-8000-000000000001',
             'en', 'liquidity', 'liquidity', 'liquidity', true),
            ('91000000-0000-4000-8000-000000000010',
             '90000000-0000-4000-8000-000000000001',
             'ko', '유동성', '유동성', 'liquidity', true),
            ('91000000-0000-4000-8000-000000000011',
             '90000000-0000-4000-8000-000000000001',
             'en', 'trading volume', 'trading volume', 'liquidity', true),
            ('91000000-0000-4000-8000-000000000012',
             '90000000-0000-4000-8000-000000000001',
             'ko', '거래량', '거래량', 'liquidity', true),
            ('91000000-0000-4000-8000-000000000013',
             '90000000-0000-4000-8000-000000000001',
             'en', 'settlement', 'settlement', 'settlement', true),
            ('91000000-0000-4000-8000-000000000014',
             '90000000-0000-4000-8000-000000000001',
             'ko', '정산', '정산', 'settlement', true),
            ('91000000-0000-4000-8000-000000000015',
             '90000000-0000-4000-8000-000000000001',
             'en', 'oracle', 'oracle', 'settlement', true),
            ('91000000-0000-4000-8000-000000000016',
             '90000000-0000-4000-8000-000000000001',
             'ko', '오라클', '오라클', 'settlement', true),
            ('91000000-0000-4000-8000-000000000017',
             '90000000-0000-4000-8000-000000000001',
             'en', 'election prediction', 'election prediction', 'event', true),
            ('91000000-0000-4000-8000-000000000018',
             '90000000-0000-4000-8000-000000000001',
             'ko', '선거 예측', '선거 예측', 'event', true),
            ('91000000-0000-4000-8000-000000000019',
             '90000000-0000-4000-8000-000000000001',
             'en', 'prediction market regulation', 'prediction market regulation',
             'regulation', true),
            ('91000000-0000-4000-8000-000000000020',
             '90000000-0000-4000-8000-000000000001',
             'ko', '예측시장 규제', '예측시장 규제', 'regulation', true)
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO post_matches (
            id,
            post_version_id,
            rule_id,
            matched,
            normalized_phrase,
            category,
            match_hash,
            matched_at
        )
        SELECT
            gen_random_uuid(),
            pv.id,
            kr.id,
            match_values.matched,
            kr.normalized_phrase,
            kr.category,
            encode(
                digest(
                    convert_to(
                        '{"category":' || to_json(kr.category)::text
                        || ',"matched":'
                        || CASE WHEN match_values.matched THEN 'true' ELSE 'false' END
                        || ',"normalized_phrase":'
                        || to_json(kr.normalized_phrase)::text
                        || ',"post_version_id":"'
                        || pv.id::text
                        || '","rule_id":"'
                        || kr.id::text
                        || '"}',
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            ),
            '2026-07-26T13:15:00Z'
        FROM post_versions pv
        JOIN posts p ON p.id = pv.post_id
        JOIN keyword_rules kr
          ON kr.rule_set_id = '90000000-0000-4000-8000-000000000001'
         AND kr.language = p.language
         AND kr.enabled
        CROSS JOIN LATERAL (
            SELECT position(
                kr.normalized_phrase IN lower(pv.title || E'\\n' || pv.body)
            ) > 0 AS matched
        ) AS match_values
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    """Preserve append-only reviewed vocabulary and match evidence."""
