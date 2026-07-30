"""Static, bound SQL for the Production runtime adapter."""

from sqlalchemy import text

READ_ONLY = text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
DATABASE_NOW = text("SELECT transaction_timestamp()")
STATE = text(
    """
    WITH latest AS (
      SELECT t.* FROM source_activation_state_transitions t
      WHERE t.activation_nonce = CAST(:nonce AS uuid)
      ORDER BY t.transition_at_db DESC, t.id DESC LIMIT 1
    )
    SELECT (SELECT version_num FROM alembic_version) AS revision,
      a.reviewed_sha, r.approved_plan_sha256,
      CAST(l.activation_nonce AS text) AS activation_nonce,
      l.state AS source_state, s.enabled AS source_enabled,
      (s.current_binding_id = l.current_binding_id
       AND s.current_cadence_id = l.current_cadence_id
       AND s.active_authorization_id = l.current_authorization_id
       AND s.current_budget_id = l.current_budget_id) AS binding_verified,
      CAST(s.id AS text) AS source_id, c.cadence_anchor_at,
      c.expires_at AS authorization_expires_at,
      a.attestation_sha256, a.free_tier_evidence_sha256
    FROM latest l JOIN community_sources s ON s.id = l.source_id
    JOIN source_activation_attestations a ON a.id = l.attestation_id
    JOIN source_cadence_epochs c ON c.id = l.current_cadence_id
    JOIN release_roots r ON r.activation_nonce = l.activation_nonce
    """
)
DCINSIDE = text(
    """
    SELECT CAST(s.id AS text) AS source_id, s.enabled,
      count(p.id) FILTER (
        WHERE p.published_at >= transaction_timestamp() - interval '90 days'
      ) AS count_90d,
      COALESCE(jsonb_agg(jsonb_build_array(
        p.id, p.current_version_id, p.published_at, p.canonical_url
      ) ORDER BY p.id) FILTER (WHERE p.id IS NOT NULL), '[]'::jsonb) AS snapshot
    FROM community_sources s LEFT JOIN posts p ON p.source_id = s.id
    WHERE s.platform::text = 'dcinside' GROUP BY s.id, s.enabled
    """
)
SEED = text(
    """
    SELECT CAST(s.id AS text) AS source_id, pv.title AS literal,
      matched.normalized_phrase AS keyword
    FROM posts p JOIN post_versions pv ON pv.id = p.current_version_id
    JOIN community_sources s ON s.id = p.source_id
    LEFT JOIN LATERAL (
      SELECT pm.normalized_phrase FROM post_matches pm
      WHERE pm.post_version_id = pv.id AND pm.matched
      ORDER BY pm.normalized_phrase LIMIT 1
    ) matched ON true
    WHERE s.platform::text = 'manifold' AND s.enabled
      AND char_length(pv.title) BETWEEN 2 AND 100
    ORDER BY p.published_at DESC, p.id DESC LIMIT 1
    """
)
COUNT = text(
    """
    SELECT count(*) AS total FROM posts p
    JOIN post_versions pv ON pv.id = p.current_version_id
    JOIN community_sources s ON s.id = p.source_id
    WHERE (CAST(:source_id AS uuid) IS NULL OR p.source_id = :source_id)
      AND (CAST(:pattern AS text) IS NULL OR
           pv.search_text COLLATE "C" LIKE :pattern ESCAPE '\\')
      AND (CAST(:keyword AS text) IS NULL OR EXISTS (
        SELECT 1 FROM post_matches pm WHERE pm.post_version_id = pv.id
          AND pm.matched AND pm.normalized_phrase ILIKE ('%' || :keyword || '%')
      ))
    """
)
PAGE = text(
    """
    SELECT CAST(p.id AS text) AS id, CAST(p.source_id AS text) AS source_id
    FROM posts p JOIN post_versions pv ON pv.id = p.current_version_id
    WHERE p.source_id = CAST(:source_id AS uuid)
      AND pv.search_text COLLATE "C" LIKE :pattern ESCAPE '\\'
    ORDER BY p.published_at DESC, p.id DESC LIMIT 50 OFFSET 0
    """
)
FRESHNESS = text(
    """
    SELECT max(p.published_at) FILTER (
        WHERE s.platform::text = 'manifold') AS latest_manifold_at,
      count(p.id) FILTER (WHERE s.platform::text = 'dcinside'
        AND p.published_at >= transaction_timestamp() - interval '30 days') > 0
        AS dcinside_recent,
      NOT EXISTS (
        SELECT 1 FROM cadence_workflow_slots slot
        JOIN source_cadence_epochs epoch ON epoch.id = slot.cadence_epoch_id
        WHERE epoch.activation_nonce = CAST(:nonce AS uuid)
          AND slot.due_at <= transaction_timestamp()
          AND slot.accepted_attempt_id IS NULL
      ) AS cadence_complete
    FROM posts p JOIN community_sources s ON s.id = p.source_id
    """
)

__all__ = (
    "COUNT",
    "DATABASE_NOW",
    "DCINSIDE",
    "FRESHNESS",
    "PAGE",
    "READ_ONLY",
    "SEED",
    "STATE",
)
