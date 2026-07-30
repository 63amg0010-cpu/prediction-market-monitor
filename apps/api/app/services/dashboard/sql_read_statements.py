"""PostgreSQL statements for posts and immutable report revisions."""

from typing import Final

from sqlalchemy import TextClause, text

POST_SEARCH_PREDICATE: Final = """
      AND (CAST(:search_pattern AS text) IS NULL OR
           pv.search_text COLLATE "C" LIKE :search_pattern ESCAPE '\\')
"""

_SEARCH_PREDICATE_MARKER: Final = "__POST_SEARCH_PREDICATE__"


def post_search_statement(statement: str) -> TextClause:
    """Compile static SQL with the shared bound literal-search predicate."""
    return text(statement.replace(_SEARCH_PREDICATE_MARKER, POST_SEARCH_PREDICATE))


POST_COUNT: Final[TextClause] = post_search_statement(
    """
    SELECT count(*) AS total_items
    FROM posts p
    JOIN post_versions pv ON pv.id = p.current_version_id
    JOIN community_sources s ON s.id = p.source_id
    WHERE (CAST(:country AS text) IS NULL OR s.country::text = :country)
      AND (CAST(:source_id AS uuid) IS NULL OR p.source_id = :source_id)
      AND (CAST(:published_from AS timestamptz) IS NULL
           OR p.published_at >= :published_from)
      AND (CAST(:published_to AS timestamptz) IS NULL
           OR p.published_at < :published_to)
      AND (CAST(:keyword AS text) IS NULL OR EXISTS (
          SELECT 1 FROM post_matches pm
          WHERE pm.post_version_id = p.current_version_id
            AND pm.matched
            AND pm.normalized_phrase ILIKE ('%' || :keyword || '%')
      ))
    __POST_SEARCH_PREDICATE__
    """
)

POST_PAGE: Final[TextClause] = post_search_statement(
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
    )
    SELECT p.id, p.source_id, s.display_name AS source_name, s.country::text AS country,
           pv.title, p.canonical_url AS original_url, p.published_at,
           a.state::text AS stored_analysis_state, a.relevance,
           a.sentiment::text AS sentiment, q.status::text AS queue_status,
           e.comments_count, e.upvote_or_score AS score
    FROM posts p
    JOIN post_versions pv ON pv.id = p.current_version_id
    JOIN community_sources s ON s.id = p.source_id
    LEFT JOIN ranked_analysis a
      ON a.post_version_id = p.current_version_id AND a.row_number = 1
    LEFT JOIN analysis_queue q ON q.post_version_id = p.current_version_id
    LEFT JOIN ranked_engagement e
      ON e.post_version_id = p.current_version_id AND e.row_number = 1
    WHERE (CAST(:country AS text) IS NULL OR s.country::text = :country)
      AND (CAST(:source_id AS uuid) IS NULL OR p.source_id = :source_id)
      AND (CAST(:published_from AS timestamptz) IS NULL
           OR p.published_at >= :published_from)
      AND (CAST(:published_to AS timestamptz) IS NULL
           OR p.published_at < :published_to)
      AND (CAST(:keyword AS text) IS NULL OR EXISTS (
          SELECT 1 FROM post_matches pm
          WHERE pm.post_version_id = p.current_version_id
            AND pm.matched
            AND pm.normalized_phrase ILIKE ('%' || :keyword || '%')
      ))
    __POST_SEARCH_PREDICATE__
    ORDER BY p.published_at DESC, p.id DESC
    LIMIT :page_size OFFSET :page_offset
    """
)

REPORT_COUNT: Final[TextClause] = text(
    """
    SELECT count(*) AS total_items
    FROM daily_reports r
    JOIN daily_report_versions v ON v.id = r.latest_version_id
    WHERE (CAST(:date_from AS date) IS NULL OR r.report_date_seoul >= :date_from)
      AND (CAST(:date_to AS date) IS NULL OR r.report_date_seoul <= :date_to)
      AND (CAST(:status AS text) IS NULL OR v.status::text = :status)
    """
)

_REPORT_PROJECTION: Final = """
    SELECT v.id, v.report_date_seoul, v.revision, v.status::text AS status,
           v.candidate_count, v.relevant_count, v.pending_count,
           v.analysis_coverage_decimal AS analysis_coverage,
           v.comments_sum, v.score_sum, v.highlights, v.rising_keywords,
           v.source_coverage, v.manifest_id, v.input_set_hash,
           m.manifest_payload_sha256, v.report_payload_sha256,
           m.input_set_hash AS manifest_input_set_hash,
           m.report_date_seoul AS manifest_report_date_seoul,
           m.report_revision AS manifest_report_revision,
           m.codec::text AS manifest_codec,
           m.compressed_payload AS compressed_manifest_payload,
           m.uncompressed_byte_length AS manifest_uncompressed_byte_length,
           v.report_payload, v.created_at
    FROM daily_reports r
    JOIN daily_report_versions v ON v.id = r.latest_version_id
    JOIN report_input_manifests m
      ON m.id = v.manifest_id AND m.report_version_id = v.id
"""

REPORT_PAGE: Final[TextClause] = text(
    _REPORT_PROJECTION
    + """
    WHERE (CAST(:date_from AS date) IS NULL OR r.report_date_seoul >= :date_from)
      AND (CAST(:date_to AS date) IS NULL OR r.report_date_seoul <= :date_to)
      AND (CAST(:status AS text) IS NULL OR v.status::text = :status)
    ORDER BY r.report_date_seoul DESC
    LIMIT :page_size OFFSET :page_offset
    """
)

REPORT_BY_DATE: Final[TextClause] = text(
    _REPORT_PROJECTION
    + """
    WHERE r.report_date_seoul = :report_date
    """
)
