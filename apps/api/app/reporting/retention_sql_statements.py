"""PostgreSQL locks and bounded discovery for restrictive report retention."""

from typing import Final

from sqlalchemy import TextClause, text

LOCK_POST_VERSION_SOURCE: Final[TextClause] = text(
    """
    SELECT version.id, version.content_hash AS source_entity_hash,
           post.source_id, post.published_at AS published_or_observed_at,
           version.collected_at AS retention_started_at
    FROM post_versions version
    JOIN posts post ON post.id = version.post_id
    WHERE version.id = :entity_id
    FOR UPDATE OF version
    """
)

LOCK_ANALYSIS_SOURCE: Final[TextClause] = text(
    """
    SELECT analysis.id, analysis.output_hash AS source_entity_hash,
           post.source_id, analysis.analyzed_at AS published_or_observed_at,
           analysis.analyzed_at AS retention_started_at
    FROM analyses analysis
    JOIN post_versions version ON version.id = analysis.post_version_id
    JOIN posts post ON post.id = version.post_id
    WHERE analysis.id = :entity_id
    FOR UPDATE OF analysis
    """
)

LOCK_MATCH_SOURCE: Final[TextClause] = text(
    """
    SELECT matched_row.id, matched_row.match_hash AS source_entity_hash,
           post.source_id, matched_row.matched_at AS published_or_observed_at,
           matched_row.matched_at AS retention_started_at
    FROM post_matches matched_row
    JOIN post_versions version ON version.id = matched_row.post_version_id
    JOIN posts post ON post.id = version.post_id
    WHERE matched_row.id = :entity_id
    FOR UPDATE OF matched_row
    """
)

LOCK_ENGAGEMENT_SOURCE: Final[TextClause] = text(
    """
    SELECT engagement.id, engagement.engagement_hash AS source_entity_hash,
           post.source_id,
           engagement.observed_at AS published_or_observed_at,
           engagement.observed_at AS retention_started_at
    FROM engagement_observations engagement
    JOIN post_versions version ON version.id = engagement.post_version_id
    JOIN posts post ON post.id = version.post_id
    WHERE engagement.id = :entity_id
    FOR UPDATE OF engagement
    """
)

LOCK_PUBLICATION_SOURCE: Final[TextClause] = text(
    """
    SELECT publication.id, publication.run_id, publication.source_id,
           publication.terminal_page_commit_id, publication.sequence,
           publication.final_chain_hash, publication.post_set_hash,
           publication.distinct_post_version_count, publication.zero_post,
           publication.committed_at,
           publication.committed_at AS published_or_observed_at,
           publication.committed_at AS retention_started_at
    FROM source_run_publication_manifests publication
    WHERE publication.id = :entity_id
    FOR UPDATE OF publication
    """
)

LOCK_POST_VERSION_REFERENCES: Final[TextClause] = text(
    """
    SELECT item.id AS reference_id, item.id AS manifest_item_id,
           item.manifest_id, item.item_kind::text AS item_kind,
           item.role::text AS role, item.ordinal, item.source_id,
           item.value_slice_sha256
    FROM report_input_manifest_items item
    WHERE item.live_post_version_id = :entity_id
    ORDER BY item.manifest_id, item.id
    FOR UPDATE OF item
    """
)

LOCK_ANALYSIS_REFERENCES: Final[TextClause] = text(
    """
    SELECT item.id AS reference_id, item.id AS manifest_item_id,
           item.manifest_id, item.item_kind::text AS item_kind,
           item.role::text AS role, item.ordinal, item.source_id,
           item.value_slice_sha256
    FROM report_input_manifest_items item
    WHERE item.live_analysis_id = :entity_id
    ORDER BY item.manifest_id, item.id
    FOR UPDATE OF item
    """
)

LOCK_ENGAGEMENT_REFERENCES: Final[TextClause] = text(
    """
    SELECT item.id AS reference_id, item.id AS manifest_item_id,
           item.manifest_id, item.item_kind::text AS item_kind,
           item.role::text AS role, item.ordinal, item.source_id,
           item.value_slice_sha256
    FROM report_input_manifest_items item
    WHERE item.live_engagement_observation_id = :entity_id
    ORDER BY item.manifest_id, item.id
    FOR UPDATE OF item
    """
)

LOCK_PUBLICATION_REFERENCES: Final[TextClause] = text(
    """
    SELECT item.id AS reference_id, item.id AS manifest_item_id,
           item.manifest_id, item.item_kind::text AS item_kind,
           item.role::text AS role, item.ordinal, item.source_id,
           item.value_slice_sha256
    FROM report_input_manifest_items item
    WHERE item.live_source_publication_manifest_id = :entity_id
    ORDER BY item.manifest_id, item.id
    FOR UPDATE OF item
    """
)

LOCK_MATCH_REFERENCES: Final[TextClause] = text(
    """
    SELECT link.id AS reference_id, item.id AS manifest_item_id,
           item.manifest_id, item.item_kind::text AS item_kind,
           item.role::text AS role, item.ordinal, item.source_id,
           item.value_slice_sha256
    FROM report_input_manifest_item_matches link
    JOIN report_input_manifest_items item ON item.id = link.manifest_item_id
    WHERE link.post_match_id = :entity_id
    ORDER BY item.manifest_id, link.id
    FOR UPDATE OF link, item
    """
)

ELIGIBLE_SOURCE_IDS: Final[TextClause] = text(
    """
    SELECT entity_id
    FROM (
        SELECT id AS entity_id, analyzed_at AS retention_started_at, 1 AS priority
        FROM analyses
        UNION ALL
        SELECT id, matched_at, 2 FROM post_matches
        UNION ALL
        SELECT id, observed_at, 3 FROM engagement_observations
        UNION ALL
        SELECT id, committed_at, 4 FROM source_run_publication_manifests
        UNION ALL
        SELECT id, collected_at, 5 FROM post_versions
    ) candidate
    WHERE retention_started_at <= :cutoff
    ORDER BY priority, retention_started_at, entity_id
    LIMIT :limit
    """
)
