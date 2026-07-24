"""PostgreSQL statements for metrics, operations, and source evidence."""

from typing import Final

from sqlalchemy import TextClause, text

DASHBOARD_METRICS: Final[TextClause] = text(
    """
    WITH ranked_analysis AS (
        SELECT a.*,
               row_number() OVER (
                   PARTITION BY a.post_version_id
                   ORDER BY a.analyzed_at DESC, a.id DESC
               ) AS row_number
        FROM analyses a
    ), ranked_engagement AS (
        SELECT e.*,
               row_number() OVER (
                   PARTITION BY e.post_version_id
                   ORDER BY e.observed_at DESC, e.id DESC
               ) AS row_number
        FROM engagement_observations e
    ), filtered_posts AS (
        SELECT p.id, p.current_version_id, p.published_at
        FROM posts p
        JOIN community_sources s ON s.id = p.source_id
        WHERE (CAST(:country AS text) IS NULL OR s.country::text = :country)
          AND (CAST(:source_id AS uuid) IS NULL OR p.source_id = :source_id)
          AND (CAST(:keyword AS text) IS NULL OR EXISTS (
              SELECT 1 FROM post_matches pm
              WHERE pm.post_version_id = p.current_version_id
                AND pm.matched
                AND pm.normalized_phrase ILIKE ('%' || :keyword || '%')
          ))
    ), current_posts AS (
        SELECT * FROM filtered_posts
        WHERE published_at >= :current_start AND published_at < :current_end
    ), previous_posts AS (
        SELECT * FROM filtered_posts
        WHERE published_at >= :previous_start AND published_at < :current_start
    )
    SELECT count(cp.id) AS current_count,
           (SELECT count(*) FROM previous_posts) AS previous_count,
           count(a.id) AS valid_count,
           count(*) FILTER (
               WHERE a.id IS NULL AND q.status::text = 'blocked_capability'
           ) AS blocked_count,
           count(*) FILTER (WHERE a.sentiment::text = 'positive') AS positive_count,
           count(*) FILTER (WHERE a.sentiment::text = 'neutral') AS neutral_count,
           count(*) FILTER (WHERE a.sentiment::text = 'negative') AS negative_count,
           sum(e.comments_count) AS comments_sum,
           count(e.comments_count) AS comments_known_count,
           sum(e.upvote_or_score) AS score_sum,
           count(e.upvote_or_score) AS score_known_count
    FROM current_posts cp
    LEFT JOIN ranked_analysis a
      ON a.post_version_id = cp.current_version_id
     AND a.row_number = 1
     AND a.state::text = 'valid'
    LEFT JOIN analysis_queue q ON q.post_version_id = cp.current_version_id
    LEFT JOIN ranked_engagement e
      ON e.post_version_id = cp.current_version_id AND e.row_number = 1
    """
)

OPERATIONS: Final[TextClause] = text(
    """
    SELECT now() AS generated_at,
           (SELECT max(completed_at) FROM collection_commands
            WHERE status::text = 'succeeded') AS last_complete_collection_at,
           (SELECT max(analyzed_at) FROM analyses
            WHERE state::text = 'valid') AS last_analysis_at,
           (SELECT count(*) FROM analysis_queue
            WHERE status::text IN ('pending', 'leased', 'failed_retryable'))
                AS pending_analysis_count,
           (SELECT count(*) FROM analysis_queue
            WHERE status::text = 'blocked_capability') AS blocked_analysis_count
    """
)

SOURCE_EVIDENCE: Final[TextClause] = text(
    """
    SELECT s.id AS source_id, s.display_name, s.country::text AS country, s.enabled,
           latest_attempt.status::text AS latest_attempt_status,
           COALESCE(
               latest_attempt.finished_at,
               latest_attempt.started_at,
               latest_attempt.created_at
           ) AS latest_attempt_finished_at,
           latest_success.finished_at AS latest_successful_run_at,
           manifest.sequence AS visible_publication_sequence,
           latest_attempt.failure_code
    FROM community_sources s
    LEFT JOIN LATERAL (
        SELECT r.status, r.finished_at, r.started_at, r.created_at, r.failure_code
        FROM collection_runs r
        WHERE r.source_id = s.id
        ORDER BY COALESCE(r.finished_at, r.started_at, r.created_at) DESC, r.id DESC
        LIMIT 1
    ) latest_attempt ON true
    LEFT JOIN LATERAL (
        SELECT r.id, r.finished_at
        FROM collection_runs r
        WHERE r.source_id = s.id AND r.status::text = 'succeeded'
        ORDER BY r.finished_at DESC NULLS LAST, r.id DESC
        LIMIT 1
    ) latest_success ON true
    LEFT JOIN source_run_publication_manifests manifest
      ON manifest.run_id = latest_success.id
    ORDER BY s.country, s.display_name, s.id
    """
)

REPEATABLE_READ: Final[TextClause] = text(
    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
)
